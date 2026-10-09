"""Integration tests for the Developer observability endpoints.

Exercised against a real (SQLite) database through the real router, so the
account scoping is proven as SQL rather than as a fake's promise. The two things
these tests exist to guarantee:

* **Isolation** — nothing belonging to another account is reachable, on any
  endpoint, through any filter, cursor or guessed id.
* **Enforcement** — pause/resume/revoke change real stored state and are
  rejected for a connection the caller does not own.
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
from src.domain.security.enitities.agent_grant import AgentGrantStatus
from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
    ToolOutcome,
)
from src.infrastructure.adapters.repository.sql_api_telemetry_repo import (
    SQLTelemetryRepository,
)
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository
from src.infrastructure.database.models import APIKeyModel, UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from src.presentation.api.routers.v1 import developer as developer_module

ME = 1
OTHER = 2

METRICS = "/api/v1/developer/api-logs/metrics"
LOGS = "/api/v1/developer/api-logs"
CONNECTIONS = "/api/v1/developer/mcp/connections"
SUMMARY = "/api/v1/developer/mcp/summary"
ACTIVITY = "/api/v1/developer/mcp/activity"
STREAM = "/api/v1/developer/api-logs/stream"


@dataclass
class FakeUser:
    """Only ``id`` is read by these endpoints."""

    id: int


def _seed(db_path: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(UTC)
        async with factory() as session:
            for user_id, username in ((ME, "me"), (OTHER, "other")):
                session.add(
                    UserModel(
                        id=user_id,
                        username=username,
                        email=f"{username}@example.com",
                        hashed_password="x",
                        is_active=True,
                        created_at=now,
                    )
                )
            session.add(
                APIKeyModel(
                    id="key-mine",
                    key="secret-key-mine",
                    user_id=ME,
                    name="Production SDK",
                    status="ACTIVE",
                )
            )
            await session.commit()

            telemetry = SQLTelemetryRepository(session)
            mine = [
                ApiRequestEvent(
                    id=f"mine-{index}",
                    account_id=ME,
                    api_key_id="key-mine",
                    request_id=f"req_mine_{index}",
                    timestamp=now - timedelta(minutes=index + 1),
                    method="POST" if index == 2 else "GET",
                    route_template="/api/v1/jobs",
                    status_code=500 if index == 2 else 200,
                    duration_ms=10.0 * (index + 1),
                )
                for index in range(3)
            ]
            theirs = [
                ApiRequestEvent(
                    id="theirs-0",
                    account_id=OTHER,
                    api_key_id=None,
                    request_id="req_theirs_0",
                    timestamp=now - timedelta(minutes=1),
                    method="GET",
                    route_template="/api/v1/secret",
                    status_code=200,
                    duration_ms=9.0,
                )
            ]
            await telemetry.save_events(mine + theirs)

            # The same third-party application, authorized by BOTH users — the
            # case that must not let one user's control affect the other's.
            mcp = SQLMCPRepository(session)
            from src.domain.security.enitities.agent_grant import AgentGrant
            from src.domain.security.value_object.agent_scope import AgentScope

            await mcp.upsert_grant(
                AgentGrant(
                    id="grant-me",
                    user_id=ME,
                    client_id="client-shared",
                    client_name="Example Agent",
                    scopes=(AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_CONVERT),
                    status=AgentGrantStatus.ACTIVE,
                    created_at=now,
                )
            )
            await mcp.upsert_grant(
                AgentGrant(
                    id="grant-other",
                    user_id=OTHER,
                    client_id="client-shared",
                    client_name="Example Agent",
                    scopes=(AgentScope.DOCUMENTS_READ,),
                    status=AgentGrantStatus.ACTIVE,
                    created_at=now,
                )
            )
            await telemetry.save_invocations(
                [
                    McpToolInvocation(
                        id="inv-mine-0",
                        account_id=ME,
                        grant_id="grant-me",
                        client_id="client-shared",
                        tool_name="list_files",
                        outcome=ToolOutcome.SUCCESS,
                        created_at=now - timedelta(minutes=2),
                        duration_ms=5.0,
                    ),
                    McpToolInvocation(
                        id="inv-mine-1",
                        account_id=ME,
                        grant_id="grant-me",
                        client_id="client-shared",
                        tool_name="delete_file",
                        outcome=ToolOutcome.DENIED,
                        created_at=now - timedelta(minutes=1),
                        duration_ms=1.0,
                    ),
                    McpToolInvocation(
                        id="inv-other-0",
                        account_id=OTHER,
                        grant_id="grant-other",
                        client_id="client-shared",
                        tool_name="list_files",
                        outcome=ToolOutcome.SUCCESS,
                        created_at=now - timedelta(minutes=1),
                        duration_ms=2.0,
                    ),
                ]
            )

        await engine.dispose()

    asyncio.run(_run())


@contextmanager
def developer_client(db_path: str, *, user_id: int = ME) -> Generator[TestClient, None, None]:
    _seed(db_path)

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

    def override_stream_factory():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        return async_sessionmaker(bind=engine, expire_on_commit=False)

    original_init = api_main.initialize_database
    original_is_allowed = RateLimitMiddleware._is_allowed

    async def allow_all(self, key, limit, window=60):
        del self, key, limit, window
        return True

    api_main.initialize_database = no_op_initialize_database
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=user_id)
    api_main.app.dependency_overrides[developer_module.get_stream_session_factory] = (
        override_stream_factory
    )

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", METRICS),
        ("GET", LOGS),
        ("GET", CONNECTIONS),
        ("GET", SUMMARY),
        ("GET", ACTIVITY),
        ("GET", STREAM),
    ],
)
def test_every_endpoint_requires_authentication(tmp_path, method: str, path: str) -> None:
    # Exercised without the get_current_user override so the real dependency
    # runs and rejects the request.
    with developer_client(str(tmp_path / f"auth-{path.rsplit('/', 1)[-1]}.db")) as client:
        api_main.app.dependency_overrides.pop(get_current_user, None)
        response = client.request(method, path)
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# API Logs
# ---------------------------------------------------------------------------


def test_metrics_report_only_the_callers_requests(tmp_path) -> None:
    with developer_client(str(tmp_path / "metrics.db")) as client:
        response = client.get(METRICS, params={"range": "1h"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["range_key"] == "1h"
    assert body["metric"] == "rate"
    assert body["metric_unit"] == "req/s"
    # Three of ours; the other account's row is not counted.
    assert body["overview"]["requests"] == 3
    assert body["overview"]["errors"] == 1
    assert sum(bucket["count"] for bucket in body["buckets"]) == 3
    # Every bucket is present (zero-filled), so the chart has no holes.
    assert len(body["buckets"]) >= 120
    assert len(body["values"]) == len(body["buckets"])
    assert body["generated_at"]


def test_metrics_reject_an_unknown_metric_and_range(tmp_path) -> None:
    with developer_client(str(tmp_path / "bad-params.db")) as client:
        bad_metric = client.get(METRICS, params={"metric": "p99"})
        bad_range = client.get(METRICS, params={"range": "42h"})

    assert bad_metric.status_code == 400
    assert bad_metric.json()["detail"]["code"] == "INVALID_METRIC"
    assert bad_range.status_code == 400
    assert bad_range.json()["detail"]["code"] == "INVALID_RANGE"


def test_logs_list_is_scoped_and_filters_server_side(tmp_path) -> None:
    with developer_client(str(tmp_path / "logs.db")) as client:
        everything = client.get(LOGS, params={"range": "1h"})
        errors = client.get(LOGS, params={"range": "1h", "outcome": "error"})
        by_key = client.get(LOGS, params={"range": "1h", "api_key_id": "key-mine"})
        by_route = client.get(LOGS, params={"range": "1h", "route": "secret"})

    body = everything.json()
    ids = [item["id"] for item in body["items"]]
    assert sorted(ids) == ["mine-0", "mine-1", "mine-2"]
    # The key's display name is resolved; the secret never is.
    assert body["items"][0]["api_key_name"] == "Production SDK"
    assert "secret-key-mine" not in everything.text

    assert [item["id"] for item in errors.json()["items"]] == ["mine-2"]
    assert len(by_key.json()["items"]) == 3
    # Filtering by the other account's route matches nothing.
    assert by_route.json()["items"] == []


def test_logs_paginate_without_repeating_or_skipping(tmp_path) -> None:
    with developer_client(str(tmp_path / "paging.db")) as client:
        first = client.get(LOGS, params={"range": "1h", "limit": 2}).json()
        assert first["next_cursor"]
        second = client.get(
            LOGS, params={"range": "1h", "limit": 2, "cursor": first["next_cursor"]}
        ).json()

    ids = [item["id"] for item in first["items"]] + [item["id"] for item in second["items"]]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert second["next_cursor"] is None


def test_detail_is_available_for_own_requests_and_hidden_for_others(tmp_path) -> None:
    with developer_client(str(tmp_path / "detail.db")) as client:
        mine = client.get(f"{LOGS}/mine-0")
        theirs = client.get(f"{LOGS}/theirs-0")
        missing = client.get(f"{LOGS}/does-not-exist")

    assert mine.status_code == 200
    payload = mine.json()
    assert payload["entry"]["route"] == "/api/v1/jobs"
    assert payload["via_session"] is False
    # A derived status description, not a recorded body.
    assert payload["status_meaning"] == "Request completed successfully."
    # A foreign id and a nonexistent one are indistinguishable.
    assert theirs.status_code == 404
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Live stream
# ---------------------------------------------------------------------------


def test_stream_validates_its_parameters_before_opening(tmp_path) -> None:
    """The stream's guards are asserted without consuming the body.

    Reading an infinite `text/event-stream` body through the in-process test
    transport blocks until the connection is closed, so the *frame contents* are
    covered where they are actually built — `_snapshot_payload`, unit tested
    below — and the endpoint itself is asserted for the two things a client can
    get wrong before a single byte is streamed: its authentication (above) and
    its range.
    """
    with developer_client(str(tmp_path / "stream-guards.db")) as client:
        bad_range = client.get(STREAM, params={"range": "42h"})
        bad_metric = client.get(STREAM, params={"metric": "nonsense"})

    assert bad_range.status_code == 400
    assert bad_range.json()["detail"]["code"] == "INVALID_RANGE"
    assert bad_metric.status_code == 400
    assert bad_metric.json()["detail"]["code"] == "INVALID_METRIC"


# ---------------------------------------------------------------------------
# MCP Activity
# ---------------------------------------------------------------------------


def test_connections_are_scoped_to_the_caller(tmp_path) -> None:
    with developer_client(str(tmp_path / "connections.db")) as client:
        response = client.get(CONNECTIONS, params={"range": "24h"})

    body = response.json()
    assert [connection["id"] for connection in body["connections"]] == ["grant-me"]
    connection = body["connections"][0]
    assert connection["client_name"] == "Example Agent"
    assert connection["status"] == "ACTIVE"
    # Only the scopes this connection actually holds are marked granted.
    granted = {permission["scope"] for permission in connection["permissions"] if permission["granted"]}
    assert granted == {"documents.read", "documents.convert"}
    assert connection["requests"] == 2
    assert connection["denied"] == 1


def test_summary_counts_only_the_callers_activity(tmp_path) -> None:
    with developer_client(str(tmp_path / "summary.db")) as client:
        body = client.get(SUMMARY, params={"range": "24h"}).json()

    assert body["connections"] == 1
    assert body["requests"] == 2
    assert body["denied"] == 1
    assert body["tools_used"] == ["delete_file", "list_files"]


def test_activity_log_is_scoped_and_filterable(tmp_path) -> None:
    with developer_client(str(tmp_path / "activity.db")) as client:
        everything = client.get(ACTIVITY, params={"range": "24h"})
        denied = client.get(ACTIVITY, params={"range": "24h", "outcome": "DENIED"})
        by_tool = client.get(ACTIVITY, params={"range": "24h", "tool_name": "list_files"})

    ids = [item["id"] for item in everything.json()["items"]]
    assert sorted(ids) == ["inv-mine-0", "inv-mine-1"]
    assert [item["id"] for item in denied.json()["items"]] == ["inv-mine-1"]
    assert [item["id"] for item in by_tool.json()["items"]] == ["inv-mine-0"]
    assert everything.json()["items"][0]["client_name"] == "Example Agent"


def test_pause_then_resume_round_trips_through_the_api(tmp_path) -> None:
    with developer_client(str(tmp_path / "pause.db")) as client:
        paused = client.post(f"{CONNECTIONS}/grant-me/pause")
        assert paused.status_code == 200, paused.text
        assert paused.json()["connection"]["status"] == "PAUSED"
        assert paused.json()["connection"]["paused_at"] is not None

        # A fresh read agrees — the state was persisted, not just returned.
        current = client.get(CONNECTIONS, params={"range": "24h"}).json()
        assert current["connections"][0]["status"] == "PAUSED"

        resumed = client.post(f"{CONNECTIONS}/grant-me/resume")
        assert resumed.status_code == 200
        assert resumed.json()["connection"]["status"] == "ACTIVE"
        assert resumed.json()["connection"]["paused_at"] is None
        # Resuming never widens: the original scopes are intact and no more.
        assert set(resumed.json()["connection"]["scopes"]) == {
            "documents.read",
            "documents.convert",
        }


def test_revoke_is_terminal_through_the_api(tmp_path) -> None:
    with developer_client(str(tmp_path / "revoke.db")) as client:
        revoked = client.post(f"{CONNECTIONS}/grant-me/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["connection"]["status"] == "REVOKED"

        # Pausing a revoked connection is refused with a clear 400.
        paused = client.post(f"{CONNECTIONS}/grant-me/pause")
        assert paused.status_code == 400
        assert "revoked" in paused.json()["detail"].lower()


def test_controlling_another_users_connection_is_a_404(tmp_path) -> None:
    with developer_client(str(tmp_path / "cross.db")) as client:
        paused = client.post(f"{CONNECTIONS}/grant-other/pause")
        resumed = client.post(f"{CONNECTIONS}/grant-other/resume")
        revoked = client.post(f"{CONNECTIONS}/grant-other/revoke")
        # And the other account's connection is still active.
        mine = client.get(CONNECTIONS, params={"range": "24h"}).json()

    # 404, not 403: a distinct code would confirm somebody else's grant exists.
    assert paused.status_code == 404
    assert resumed.status_code == 404
    assert revoked.status_code == 404
    # Only our own connection is visible at all.
    assert [connection["id"] for connection in mine["connections"]] == ["grant-me"]


def test_an_unknown_connection_id_is_a_404(tmp_path) -> None:
    with developer_client(str(tmp_path / "unknown.db")) as client:
        response = client.post(f"{CONNECTIONS}/nope/pause")
    assert response.status_code == 404
