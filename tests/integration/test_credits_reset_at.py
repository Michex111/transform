"""Endpoint tests for the additive ``credits_reset_at`` field.

Monthly credits are bucketed by UTC calendar month, so a user's credits reset
implicitly at the first instant of the next UTC month. Both the dashboard and
the credit-balance responses expose that instant (and ``None`` for tiers with
no persistent monthly credits — nothing resets for those users).

Uses the same ``TestClient`` + SQLite override pattern as
``test_dashboard_storage_breakdown.py``, seeding the subscription tier (and
optionally a credit bucket) through the real repositories.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_credit_repo import SQLCreditRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.auth_dependencies import get_current_user

USER_ID = 1
DASHBOARD_PATH = "/api/v1/user/dashboard"
BALANCE_PATH = "/api/v1/credits/balance"

# Pre-existing response keys — the new field must be purely additive.
DASHBOARD_KEYS = {
    "conversion_stats",
    "storage_stats",
    "credit_balance",
    "tier",
    "recent_jobs_count",
    "active_api_keys",
    "credits_reset_at",
}
BALANCE_KEYS = {
    "balance",
    "tier",
    "monthly_allowance",
    "monthly_remaining",
    "credits_reset_at",
}


@dataclass
class FakeUser:
    id: int


def _next_month_start(now: datetime) -> datetime:
    """Independent oracle: first instant of the UTC month after ``now``."""
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def _current_period_key() -> str:
    """Independent oracle for the credited month (the existing bucket format)."""
    return datetime.now(UTC).strftime("%Y-%m")


def _month_start_after_key(period_key: str) -> datetime:
    """The first instant of the month following a ``"YYYY-MM"`` period key."""
    year, month = (int(part) for part in period_key.split("-"))
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime(next_year, next_month, 1, tzinfo=UTC)


def _seed(
    db_path: str,
    *,
    tier: SubscriptionTier,
    credit: tuple[int, int] | None = None,
) -> None:
    """Create the schema, the user, its tier, and optionally a credit bucket.

    ``credit`` is an ``(allowance, remaining)`` pair saved against the current
    UTC month's period key.
    """

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                UserModel(
                    id=USER_ID,
                    username="reset-user",
                    email="reset@example.com",
                    hashed_password="x",
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            await SQLSubscriptionRepository(session).upsert_subscription(
                actor_key=f"user:{USER_ID}", user_id=USER_ID, tier=tier
            )
            if credit is not None:
                allowance, remaining = credit
                await SQLCreditRepository(session).save_credit(
                    Credit(
                        owner_id=str(USER_ID),
                        period_key=_current_period_key(),
                        allowance=allowance,
                        remaining=remaining,
                    )
                )
        await engine.dispose()

    asyncio.run(_run())


@contextmanager
def reset_client(
    db_path: str,
    *,
    tier: SubscriptionTier = SubscriptionTier.FREE,
    credit: tuple[int, int] | None = None,
) -> Generator[TestClient, None, None]:
    _seed(db_path, tier=tier, credit=credit)

    async def no_op_initialize_database() -> None:
        return None

    async def override_db():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=USER_ID)

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def _get(client: TestClient, path: str) -> tuple[dict, set[datetime]]:
    """GET ``path`` and return the payload plus the acceptable reset instants.

    The expected instants are computed either side of the request so the test
    cannot flake if it happens to straddle a UTC month boundary.
    """
    before = _next_month_start(datetime.now(UTC))
    response = client.get(path)
    after = _next_month_start(datetime.now(UTC))
    assert response.status_code == 200, response.text
    return response.json(), {before, after}


def _parse_reset(body: dict) -> datetime:
    """Assert the field is a well-formed, offset-carrying ISO-8601 UTC string."""
    value = body["credits_reset_at"]
    assert isinstance(value, str), f"expected an ISO-8601 string, got {value!r}"
    # An explicit offset keeps `new Date(...)` unambiguous in the browser.
    assert value.endswith("Z") or value.endswith("+00:00"), value
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{value!r} is not timezone-aware"
    assert parsed.utcoffset() == timedelta(0), f"{value!r} is not UTC"
    assert (parsed.microsecond, parsed.second, parsed.minute, parsed.hour) == (0, 0, 0, 0)
    assert parsed.day == 1
    return parsed


# ---------------------------------------------------------------------------
# Present and correctly shaped for tiers with persistent monthly credits
# ---------------------------------------------------------------------------

def test_dashboard_credits_reset_at_is_the_first_of_the_next_utc_month(tmp_path) -> None:
    with reset_client(str(tmp_path / "dash.db")) as client:
        body, acceptable = _get(client, DASHBOARD_PATH)

    assert _parse_reset(body) in acceptable


def test_credit_balance_credits_reset_at_is_the_first_of_the_next_utc_month(tmp_path) -> None:
    with reset_client(str(tmp_path / "balance.db")) as client:
        body, acceptable = _get(client, BALANCE_PATH)

    assert _parse_reset(body) in acceptable


def test_credits_reset_at_never_precedes_now(tmp_path) -> None:
    """A reset in the past would make the UI claim credits already rolled over."""
    with reset_client(str(tmp_path / "future.db")) as client:
        dashboard, _ = _get(client, DASHBOARD_PATH)
        balance, _ = _get(client, BALANCE_PATH)

    now = datetime.now(UTC)
    assert _parse_reset(dashboard) > now
    assert _parse_reset(balance) > now


# ---------------------------------------------------------------------------
# Null for tiers without persistent monthly credits (GUEST, ENTERPRISE)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tier", [SubscriptionTier.GUEST, SubscriptionTier.ENTERPRISE])
def test_dashboard_credits_reset_at_is_null_without_persistent_credits(
    tmp_path, tier: SubscriptionTier
) -> None:
    with reset_client(str(tmp_path / f"dash-{tier}.db"), tier=tier) as client:
        payload, _ = _get(client, DASHBOARD_PATH)

    assert "credits_reset_at" in payload
    assert payload["credits_reset_at"] is None
    # Nothing resets for these tiers: no allowance, so no balance either.
    assert payload["credit_balance"] == 0


@pytest.mark.parametrize("tier", [SubscriptionTier.GUEST, SubscriptionTier.ENTERPRISE])
def test_credit_balance_credits_reset_at_is_null_without_persistent_credits(
    tmp_path, tier: SubscriptionTier
) -> None:
    with reset_client(str(tmp_path / f"balance-{tier}.db"), tier=tier) as client:
        payload, _ = _get(client, BALANCE_PATH)

    assert "credits_reset_at" in payload
    assert payload["credits_reset_at"] is None
    # Existing GUEST behaviour is preserved: allowance 0, not null.
    assert payload["monthly_allowance"] == 0
    assert payload["monthly_remaining"] == 0
    assert payload["balance"] == 0


def test_guest_tier_has_no_reset_date_while_free_tier_does(tmp_path) -> None:
    """The null is decided by the tier policy, not by the request path."""
    with reset_client(str(tmp_path / "guest.db"), tier=SubscriptionTier.GUEST) as client:
        guest, _ = _get(client, BALANCE_PATH)
    with reset_client(str(tmp_path / "free.db"), tier=SubscriptionTier.FREE) as client:
        free, _ = _get(client, BALANCE_PATH)

    assert guest["credits_reset_at"] is None
    assert free["credits_reset_at"] is not None
    assert free["monthly_allowance"] == 50  # FREE policy allowance, unchanged


# ---------------------------------------------------------------------------
# Purely additive: no pre-existing field changed
# ---------------------------------------------------------------------------

def test_dashboard_pre_existing_fields_are_unchanged(tmp_path) -> None:
    with reset_client(str(tmp_path / "dash-keys.db"), credit=(50, 42)) as client:
        body, _ = _get(client, DASHBOARD_PATH)

    assert set(body) == DASHBOARD_KEYS
    # The bucket for the current UTC month is still the one that is read.
    assert body["credit_balance"] == 42
    assert body["tier"] == "FREE"
    assert body["recent_jobs_count"] == 0
    assert body["active_api_keys"] == 0
    assert body["conversion_stats"] == {
        "total_jobs": 0,
        "successful_jobs": 0,
        "failed_jobs": 0,
        "total_credits_used": 0,
    }
    assert set(body["storage_stats"]) == {
        "used_bytes",
        "limit_bytes",
        "used_percent",
        "file_count",
        "breakdown",
        # Added deliberately for the large-upload work: the SPA reads the
        # headroom and the per-tier per-file cap from here instead of hardcoding
        # them. Both are additive, so an older bundle is unaffected.
        "available_bytes",
        "max_file_size_bytes",
    }
    assert body["storage_stats"]["limit_bytes"] == 5 * 1024 * 1024 * 1024  # FREE quota


def test_credit_balance_pre_existing_fields_are_unchanged(tmp_path) -> None:
    with reset_client(str(tmp_path / "balance-keys.db"), credit=(50, 42)) as client:
        with_credit, _ = _get(client, BALANCE_PATH)
    with reset_client(str(tmp_path / "balance-fresh.db")) as client:
        fresh_bucket, _ = _get(client, BALANCE_PATH)

    assert set(with_credit) == BALANCE_KEYS
    assert with_credit["balance"] == 42
    assert with_credit["monthly_remaining"] == 42
    assert with_credit["monthly_allowance"] == 50
    assert with_credit["tier"] == "FREE"

    # No bucket yet for this month -> the tier allowance is the fallback.
    assert fresh_bucket["balance"] == 50
    assert fresh_bucket["monthly_remaining"] == 50
    assert fresh_bucket["monthly_allowance"] == 50


def test_reset_date_agrees_with_the_period_being_bucketed(tmp_path) -> None:
    """The reset instant opens the month after the bucket that is read.

    ``credits_reset_at`` must come from the same period arithmetic as the
    period key used to look up the credit bucket, otherwise the date shown to
    users could drift from the credits actually in play.
    """
    with reset_client(str(tmp_path / "agree.db"), credit=(50, 42)) as client:
        before_key = _current_period_key()
        body, _ = _get(client, BALANCE_PATH)
        after_key = _current_period_key()

    # The seeded bucket (current UTC month) is the one that was read ...
    assert body["monthly_remaining"] == 42
    # ... and the reset instant opens exactly the month after that key.
    assert _parse_reset(body) in {
        _month_start_after_key(before_key),
        _month_start_after_key(after_key),
    }
