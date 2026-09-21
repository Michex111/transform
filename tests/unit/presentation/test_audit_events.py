"""Verify the security events the ISO 27001 pack claims are emitted.

``docs/security/access-control-policy.md`` lists ``rate_limited``,
``permission_denied`` and ``data_access`` as implemented audit events, and
``incident-response-plan.md`` / ``risk-assessment.md`` tell an auditor to
trigger a 429, a 404 ownership denial and a download and look for them. Each of
those was documented but never emitted, so these tests pin the wiring.

The events are asserted through the real ``audit_logger`` (its own handler and
``propagate = False`` mean ``caplog`` cannot see them), which also proves the
JSON schema carries the documented fields.
"""

import json
import logging

import pytest
from fastapi import HTTPException

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.infrastructure.logging.audit import audit_logger
from src.presentation.api.dependencies.job_access import assert_job_owner


class _AuditCapture(logging.Handler):
    """Collect audit records, and parse each formatted line as JSON."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(json.loads(self.format(record)))


@pytest.fixture
def audit_events():
    handler = _AuditCapture()
    handler.setFormatter(audit_logger.handlers[0].formatter)
    audit_logger.addHandler(handler)
    try:
        yield handler.events
    finally:
        audit_logger.removeHandler(handler)


def _job(job_id: str = "job-1", user_id: int | None = 7) -> ConversionJob:
    return ConversionJob(
        job_id=job_id,
        user_id=user_id,
        conversion=None,  # type: ignore[arg-type] — unused by the ownership check
        input_file="report.pdf",
        object_key="upload/abc/report.pdf",
    )


def test_permission_denied_is_emitted_for_a_foreign_job(audit_events) -> None:
    with pytest.raises(HTTPException) as raised:
        assert_job_owner(_job(user_id=7), user_id=99)

    assert raised.value.status_code == 404
    assert [event["event"] for event in audit_events] == ["permission_denied"]
    assert audit_events[0]["resource"] == "conversion_job"
    assert audit_events[0]["job_id"] == "job-1"
    assert audit_events[0]["actor"] == "99"


def test_permission_denied_is_emitted_for_an_ownerless_guest_job(audit_events) -> None:
    """Guest jobs must not be reachable through the authenticated router."""
    with pytest.raises(HTTPException):
        assert_job_owner(_job(user_id=None), user_id=99)

    assert [event["event"] for event in audit_events] == ["permission_denied"]


def test_allowed_access_emits_no_denial(audit_events) -> None:
    assert_job_owner(_job(user_id=7), user_id=7)

    assert audit_events == []


def test_rate_limited_is_emitted_with_a_redacted_key(audit_events) -> None:
    """The 429 path must log the bucket *type*, never the full client key."""
    from src.presentation.api.middleware.rate_limit import (
        _truncate_rate_limit_key,
    )
    from src.infrastructure.logging.audit import log_rate_limited

    log_rate_limited(
        scope="/api/users/token",
        key=_truncate_rate_limit_key("ip:203.0.113.9"),
        limit=10,
    )

    assert _truncate_rate_limit_key("ip:203.0.113.9") == "ip:…"
    assert _truncate_rate_limit_key("apikey:" + "a" * 64) == "apikey:…"
    assert [event["event"] for event in audit_events] == ["rate_limited"]
    event = audit_events[0]
    assert event["scope"] == "/api/users/token"
    assert event["limit"] == 10
    # The identifying part is gone.
    assert "203.0.113.9" not in json.dumps(event)


def test_data_access_is_emitted_with_the_documented_fields(audit_events) -> None:
    from src.infrastructure.logging.audit import log_data_access

    log_data_access(
        user_id="7", action="download", resource="library_file", file_id="f-1"
    )

    assert [event["event"] for event in audit_events] == ["data_access"]
    event = audit_events[0]
    assert (event["user_id"], event["action"], event["resource"]) == (
        "7",
        "download",
        "library_file",
    )


def test_download_endpoints_call_the_audit_helper() -> None:
    """Guard against the wiring being dropped from the routers again."""
    import inspect

    from src.presentation.api.routers.v1 import conversions, files

    for module in (conversions, files):
        source = inspect.getsource(module)
        assert "log_data_access(" in source, f"{module.__name__} lost its data_access event"


class _FakeRequest:
    def __init__(self, path: str, headers: dict | None = None, host: str = "1.2.3.4"):
        self.url = type("URL", (), {"path": path})()
        self.headers = headers or {}
        self.client = type("Client", (), {"host": host})()
        self.method = "GET"


def test_dispatch_emits_rate_limited_when_the_budget_is_exhausted(audit_events) -> None:
    """The documented 429 verification step must produce the event.

    docs/security/control-implementation-matrix.md: "Show a 429 — exceed an auth
    rate limit and show `rate_limited`".
    """
    import asyncio

    from src.presentation.api.middleware.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware.__new__(RateLimitMiddleware)  # type: ignore[no-untyped-call]
    mw._settings = type(  # type: ignore[assignment]
        "S",
        (),
        {
            "RATE_LIMIT_FREE": 30,
            "RATE_LIMIT_GUEST": 10,
            "RATE_LIMIT_AUTH": 10,
            "RATE_LIMIT_AUTHENTICATED": 600,
            "RATE_LIMIT_API_KEY_DEFAULT": 1000,
        },
    )()
    mw._redis_limiter = None  # type: ignore[assignment]
    mw._store = {}  # type: ignore[assignment]
    mw._redis_unavailable = False  # type: ignore[assignment]

    async def never_allowed(key: str, limit: int) -> bool:
        return False

    mw._is_allowed = never_allowed  # type: ignore[method-assign]

    async def call_next(request):  # pragma: no cover - not reached when denied
        raise AssertionError("call_next must not run for a rate-limited request")

    response = asyncio.run(mw.dispatch(_FakeRequest("/api/v1/credits/balance"), call_next))  # type: ignore[arg-type]

    assert response.status_code == 429
    assert [event["event"] for event in audit_events] == ["rate_limited"]
    assert audit_events[0]["scope"] == "/api/v1/credits/balance"
    assert audit_events[0]["limit"] == 30


def test_spa_paths_are_not_rate_limited_and_emit_nothing(audit_events) -> None:
    """Requests outside /api/ bypass the limiter entirely (no audit noise)."""
    import asyncio

    from src.presentation.api.middleware.rate_limit import RateLimitMiddleware

    mw = RateLimitMiddleware.__new__(RateLimitMiddleware)  # type: ignore[no-untyped-call]
    mw._settings = type("S", (), {})()  # type: ignore[assignment]

    async def call_next(request):
        return "passed-through"

    assert asyncio.run(mw.dispatch(_FakeRequest("/assets/index.js"), call_next)) == "passed-through"  # type: ignore[arg-type]
    assert audit_events == []

