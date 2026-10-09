"""Tests for the SQL telemetry repository, against in-memory SQLite.

SQLite is the dialect the aggregation runs its **portable fallback** on (the
percentile helper rather than ``percentile_cont``), so these tests exercise the
path production does not take — which is exactly why they are worth having: it
is the code that would otherwise only run in CI-less environments, and it must
agree with the SQL path.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.ports.api_telemetry_port import ApiLogFilters, McpActivityFilters
from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
    ToolOutcome,
)
from src.infrastructure.adapters.repository.sql_api_telemetry_repo import (
    SQLTelemetryRepository,
)
from src.infrastructure.database.models import (
    APIKeyModel,
    ApiRequestEventModel,
    UserModel,
)
from src.infrastructure.database.session import Base

BASE = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


@contextmanager
def sqlite_session_factory():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _seed_user(factory, username: str) -> int:
    async with factory() as session:
        user = UserModel(
            username=username,
            email=f"{username}@example.com",
            hashed_password="x",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _seed_key(factory, *, key_id: str, user_id: int, name: str) -> None:
    async with factory() as session:
        session.add(
            APIKeyModel(
                id=key_id,
                key=f"secret-{key_id}",
                user_id=user_id,
                name=name,
                status="ACTIVE",
            )
        )
        await session.commit()


def _event(
    *,
    account_id: int,
    index: int = 0,
    occurred_at: datetime = BASE,
    status_code: int = 200,
    duration_ms: float = 10.0,
    method: str = "GET",
    route: str = "/api/v1/files/{file_id}",
    api_key_id: str | None = None,
) -> ApiRequestEvent:
    return ApiRequestEvent(
        id=f"evt-{account_id}-{index}",
        account_id=account_id,
        api_key_id=api_key_id,
        request_id=f"req_{account_id}_{index}",
        timestamp=occurred_at,
        method=method,
        route_template=route,
        status_code=status_code,
        duration_ms=duration_ms,
    )


async def _save(factory, events: list[ApiRequestEvent]) -> None:
    async with factory() as session:
        await SQLTelemetryRepository(session).save_events(events)


# ---------------------------------------------------------------------------
# Write + read round trip
# ---------------------------------------------------------------------------


def test_save_and_list_round_trip_including_key_name() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "acct-a")
            await _seed_key(factory, key_id="key-1", user_id=account, name="Production SDK")
            await _save(
                factory,
                [
                    _event(
                        account_id=account,
                        occurred_at=BASE + timedelta(seconds=30),
                        api_key_id="key-1",
                    )
                ],
            )

            async with factory() as session:
                page = await SQLTelemetryRepository(session).list_events(
                    account, start=BASE, end=BASE + timedelta(minutes=5)
                )

            assert len(page.items) == 1
            entry = page.items[0]
            # The display name is joined at read time; the secret never is.
            assert entry.api_key_name == "Production SDK"
            assert entry.api_key_id == "key-1"
            assert entry.route_template == "/api/v1/files/{file_id}"
            assert page.next_cursor is None

        asyncio.run(_run())


def test_account_isolation_on_reads() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            mine = await _seed_user(factory, "mine")
            theirs = await _seed_user(factory, "theirs")
            await _save(factory, [_event(account_id=theirs, index=1), _event(account_id=mine, index=2)])

            async with factory() as session:
                repository = SQLTelemetryRepository(session)
                page = await repository.list_events(
                    mine, start=BASE - timedelta(minutes=1), end=BASE + timedelta(minutes=1)
                )
                # An id that exists but belongs to somebody else is reported
                # exactly like a nonexistent one.
                foreign = await repository.get_event(mine, "evt-" + str(theirs) + "-1")
                own = await repository.get_event(mine, "evt-" + str(mine) + "-2")

            assert [item.account_id for item in page.items] == [mine]
            assert foreign is None
            assert own is not None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_aggregate_counts_errors_and_percentiles() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "agg")
            events = [ _event(account_id=account, index=i, duration_ms=float(i + 1)) for i in range(10) ]
            events.append(
                _event(account_id=account, index=99, status_code=500, duration_ms=100.0)
            )
            await _save(factory, events)

            async with factory() as session:
                buckets = await SQLTelemetryRepository(session).aggregate(
                    account,
                    start=BASE - timedelta(seconds=5),
                    end=BASE + timedelta(seconds=5),
                    bucket_seconds=60,
                )

            assert len(buckets) == 1
            bucket = buckets[0]
            assert bucket.count == 11
            assert bucket.errors == 1
            assert bucket.success == 10
            # 11 samples: p50 is the median, p95 is near the top. The exact
            # values come from the same interpolation the domain test pins.
            assert bucket.p50_ms is not None and 5.0 <= bucket.p50_ms <= 7.0
            assert bucket.p95_ms is not None and bucket.p95_ms > 50.0

        asyncio.run(_run())


def test_aggregate_splits_into_aligned_buckets() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "buckets")
            await _save(
                factory,
                [
                    _event(account_id=account, index=0, occurred_at=BASE),
                    _event(account_id=account, index=1, occurred_at=BASE + timedelta(seconds=61)),
                    _event(account_id=account, index=2, occurred_at=BASE + timedelta(seconds=122)),
                ],
            )

            async with factory() as session:
                buckets = await SQLTelemetryRepository(session).aggregate(
                    account,
                    start=BASE,
                    end=BASE + timedelta(seconds=180),
                    bucket_seconds=60,
                )

            # Bucket starts are aligned to the epoch grid, so the counts land in
            # whichever minute each instant falls into — not in three even slots.
            assert sum(bucket.count for bucket in buckets) == 3
            for bucket in buckets:
                assert bucket.start.second == 0

        asyncio.run(_run())


def test_aggregate_filters_narrow_the_result() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "filters")
            await _seed_key(factory, key_id="key-x", user_id=account, name="Key X")
            await _save(
                factory,
                [
                    _event(account_id=account, index=0, status_code=200, api_key_id="key-x"),
                    _event(account_id=account, index=1, status_code=500, api_key_id="key-x"),
                    _event(account_id=account, index=2, status_code=500, method="POST", route="/api/v1/jobs"),
                ],
            )

            async with factory() as session:
                repository = SQLTelemetryRepository(session)
                errors = await repository.aggregate(
                    account,
                    start=BASE - timedelta(seconds=5),
                    end=BASE + timedelta(seconds=5),
                    bucket_seconds=60,
                    filters=ApiLogFilters(outcome="error"),
                )
                by_class = await repository.aggregate(
                    account,
                    start=BASE - timedelta(seconds=5),
                    end=BASE + timedelta(seconds=5),
                    bucket_seconds=60,
                    filters=ApiLogFilters(status="5xx"),
                )
                by_key = await repository.aggregate(
                    account,
                    start=BASE - timedelta(seconds=5),
                    end=BASE + timedelta(seconds=5),
                    bucket_seconds=60,
                    filters=ApiLogFilters(api_key_id="key-x"),
                )
                by_route = await repository.aggregate(
                    account,
                    start=BASE - timedelta(seconds=5),
                    end=BASE + timedelta(seconds=5),
                    bucket_seconds=60,
                    filters=ApiLogFilters(route_query="jobs", method="post"),
                )

            assert sum(b.count for b in errors) == 2
            assert sum(b.count for b in by_class) == 2
            assert sum(b.count for b in by_key) == 2
            assert sum(b.count for b in by_route) == 1

        asyncio.run(_run())


def test_count_in_range_only_counts_the_window() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "windowed")
            await _save(
                factory,
                [
                    _event(account_id=account, index=0, occurred_at=BASE - timedelta(minutes=10)),
                    _event(account_id=account, index=1, occurred_at=BASE),
                ],
            )
            async with factory() as session:
                count = await SQLTelemetryRepository(session).count_in_range(
                    account, start=BASE - timedelta(seconds=30), end=BASE + timedelta(seconds=30)
                )
            assert count == 1

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Keyset pagination
# ---------------------------------------------------------------------------


def test_keyset_pagination_visits_every_row_exactly_once() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "paged")
            # Two rows share a timestamp: the id tiebreaker is what stops the
            # boundary from skipping or repeating one of them.
            events = [
                _event(account_id=account, index=i, occurred_at=BASE + timedelta(seconds=i // 2))
                for i in range(7)
            ]
            await _save(factory, events)

            seen: list[str] = []
            cursor = None
            pages = 0
            async with factory() as session:
                repository = SQLTelemetryRepository(session)
                while True:
                    page = await repository.list_events(
                        account,
                        start=BASE - timedelta(minutes=1),
                        end=BASE + timedelta(minutes=5),
                        cursor=cursor,
                        limit=2,
                    )
                    seen.extend(item.id for item in page.items)
                    pages += 1
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                    assert pages < 10  # guard against a cursor that never ends

            assert pages == 4
            assert len(seen) == 7
            assert len(set(seen)) == 7

        asyncio.run(_run())


def test_a_malformed_cursor_is_treated_as_the_first_page() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "badcursor")
            await _save(factory, [_event(account_id=account, index=0)])
            async with factory() as session:
                page = await SQLTelemetryRepository(session).list_events(
                    account,
                    start=BASE - timedelta(minutes=1),
                    end=BASE + timedelta(minutes=1),
                    cursor="!!!not-base64!!!",
                )
            # A client-side corruption must not 500 the explorer.
            assert len(page.items) == 1

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# MCP invocations
# ---------------------------------------------------------------------------


def _invocation(
    *, account_id: int, index: int, grant_id: str, outcome: str, tool: str = "list_files"
) -> McpToolInvocation:
    return McpToolInvocation(
        id=f"inv-{account_id}-{index}",
        account_id=account_id,
        grant_id=grant_id,
        client_id="client-1",
        tool_name=tool,
        outcome=outcome,
        created_at=BASE + timedelta(seconds=index),
        duration_ms=5.0,
    )


def test_invocation_totals_and_filters() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "mcp")
            await _save(factory, [])
            async with factory() as session:
                repository = SQLTelemetryRepository(session)
                await repository.save_invocations(
                    [
                        _invocation(account_id=account, index=0, grant_id="g1", outcome=ToolOutcome.SUCCESS),
                        _invocation(account_id=account, index=1, grant_id="g1", outcome=ToolOutcome.ERROR),
                        _invocation(
                            account_id=account,
                            index=2,
                            grant_id="g2",
                            outcome=ToolOutcome.DENIED,
                            tool="delete_file",
                        ),
                    ]
                )

                totals = await repository.invocation_totals(
                    account, start=BASE - timedelta(minutes=1), end=BASE + timedelta(minutes=1)
                )
                by_grant = await repository.invocation_totals_by_grant(
                    account, start=BASE - timedelta(minutes=1), end=BASE + timedelta(minutes=1)
                )
                denied = await repository.list_invocations(
                    account,
                    start=BASE - timedelta(minutes=1),
                    end=BASE + timedelta(minutes=1),
                    filters=McpActivityFilters(outcome=ToolOutcome.DENIED),
                )
                last = await repository.last_invocation_times(account, ["g1", "g2"])

            assert (totals.total, totals.successes, totals.errors, totals.denied) == (3, 1, 1, 1)
            assert by_grant["g1"].total == 2
            assert by_grant["g2"].denied == 1
            assert [item.tool_name for item in denied.items] == ["delete_file"]
            assert last["g1"] == BASE + timedelta(seconds=1)
            assert last["g2"] == BASE + timedelta(seconds=2)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_retention_deletes_only_rows_past_the_cutoff() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "retention")
            await _save(
                factory,
                [
                    _event(account_id=account, index=0, occurred_at=BASE - timedelta(days=40)),
                    _event(account_id=account, index=1, occurred_at=BASE),
                ],
            )
            async with factory() as session:
                repository = SQLTelemetryRepository(session)
                removed = await repository.delete_events_before(BASE - timedelta(days=30))
                remaining = (
                    await session.execute(select(ApiRequestEventModel))
                ).scalars().all()

            assert removed == 1
            assert [row.id for row in remaining] == ["evt-" + str(account) + "-1"]

        asyncio.run(_run())
