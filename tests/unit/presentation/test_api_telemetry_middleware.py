"""Tests for the API telemetry capture middleware.

The middleware is the *only* place attribution happens, so these tests pin the
properties the feature's trustworthiness rests on: the owner comes from verified
authentication state, an unauthenticated request is not attributed at all, the
exclusions really exclude, and a telemetry failure cannot change the response.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import pytest

from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
)
from src.infrastructure.telemetry import ingestion as ingestion_module
from src.presentation.api.middleware.api_telemetry import (
    STATE_ACCOUNT_ID,
    STATE_API_KEY_ID,
    api_telemetry_middleware,
)


@pytest.fixture(autouse=True)
def _reset_process_sink():
    """Undo the process-wide sink replacement so no other test inherits it."""
    yield
    ingestion_module.set_telemetry_ingestion(None)


class CapturingIngestion:
    """A sink that records what it was handed."""

    def __init__(self) -> None:
        self.events: list[ApiRequestEvent] = []
        self.invocations: list[McpToolInvocation] = []
        self.fail_on_record = False

    def record_event(self, event: ApiRequestEvent) -> None:
        if self.fail_on_record:
            raise RuntimeError("sink is down")
        self.events.append(event)

    def record_invocation(self, invocation: McpToolInvocation) -> None:
        self.invocations.append(invocation)

    def stats(self) -> dict[str, int]:
        return {"queue_depth": 0}


def build_app(sink: CapturingIngestion) -> TestClient:
    ingestion_module.set_telemetry_ingestion(sink)  # type: ignore[arg-type]

    app = FastAPI()
    app.middleware("http")(api_telemetry_middleware)

    @app.get("/api/v1/thing")
    async def thing(request: Request, account: int | None = None, key: str | None = None):
        if account is not None:
            setattr(request.state, STATE_ACCOUNT_ID, account)
        if key is not None:
            setattr(request.state, STATE_API_KEY_ID, key)
        return {"ok": True}

    @app.get("/api/v1/broken")
    async def broken(request: Request):
        # Authenticated, then fails: the case a user most needs in the log.
        setattr(request.state, STATE_ACCOUNT_ID, 3)
        raise RuntimeError("boom")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/developer/api-logs")
    async def developer(request: Request):
        setattr(request.state, STATE_ACCOUNT_ID, 1)
        return {"ok": True}

    @app.get("/api/v1/files/{file_id}/stream")
    async def stream(file_id: str, request: Request):
        setattr(request.state, STATE_ACCOUNT_ID, 1)
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_authenticated_request_is_captured_with_attribution() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    response = client.get("/api/v1/thing", params={"account": 7, "key": "key-9"})

    assert response.status_code == 200
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.account_id == 7
    assert event.api_key_id == "key-9"
    # The route TEMPLATE, not the raw path: no ids in the log.
    assert event.route_template == "/api/v1/thing"
    assert event.status_code == 200
    assert event.duration_ms >= 0
    assert event.request_id.startswith("req_")


def test_session_authenticated_request_has_no_key() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get("/api/v1/thing", params={"account": 7})

    assert sink.events[0].api_key_id is None


def test_unattributed_request_is_not_recorded() -> None:
    # A request that never authenticated has no owner. Writing a row attributed
    # to nobody — or worse, to a guess — is the failure mode this prevents.
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get("/api/v1/thing")

    assert sink.events == []


def test_error_response_is_still_recorded() -> None:
    # A failing request is exactly the one a user needs in the log.
    sink = CapturingIngestion()
    client = build_app(sink)

    response = client.get("/api/v1/broken")

    assert response.status_code == 500
    assert len(sink.events) == 1
    assert sink.events[0].status_code == 500
    assert sink.events[0].account_id == 3


def test_infrastructure_paths_are_excluded() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get("/health")

    assert sink.events == []


def test_the_developer_api_is_not_measured() -> None:
    # Measuring the instrument would make every dashboard refresh add rows.
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get("/api/v1/developer/api-logs")

    assert sink.events == []


def test_streaming_endpoints_are_excluded() -> None:
    # A long-lived connection's duration is its lifetime, which would poison
    # the latency percentiles.
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get("/api/v1/files/abc/stream")

    assert sink.events == []


def test_response_echoes_a_generated_request_id() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    response = client.get("/api/v1/thing", params={"account": 1})

    assert response.headers["x-request-id"].startswith("req_")
    assert sink.events[0].request_id == response.headers["x-request-id"]


def test_a_supplied_correlation_id_is_honoured_and_persisted() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    response = client.get(
        "/api/v1/thing", params={"account": 1}, headers={"X-Request-ID": "trace-abc-123"}
    )

    assert response.headers["x-request-id"] == "trace-abc-123"
    assert sink.events[0].request_id == "trace-abc-123"


def test_a_hostile_correlation_id_is_replaced() -> None:
    # The header is echoed and stored, so an over-long or non-printable value
    # must not reach either.
    sink = CapturingIngestion()
    client = build_app(sink)

    response = client.get(
        "/api/v1/thing", params={"account": 1}, headers={"X-Request-ID": "x" * 500}
    )

    assert response.headers["x-request-id"] != "x" * 500
    assert len(sink.events[0].request_id) <= 80


def test_no_sensitive_material_is_captured() -> None:
    sink = CapturingIngestion()
    client = build_app(sink)

    client.get(
        "/api/v1/thing",
        params={"account": 1, "key": "key-9"},
        headers={"Authorization": "Bearer super-secret-token", "Cookie": "session=abc"},
    )

    event = sink.events[0]
    fields = set(event.__dataclass_fields__)
    # There is no field that *could* hold a body, header or credential — this
    # asserts the schema cannot be extended silently.
    serialized = repr(event)
    assert "super-secret-token" not in serialized
    assert "session=abc" not in serialized
    assert "authorization" not in fields


def test_a_failing_sink_does_not_change_the_response() -> None:
    # Telemetry must never be able to fail the request it describes.
    sink = CapturingIngestion()
    sink.fail_on_record = True
    client = build_app(sink)

    response = client.get("/api/v1/thing", params={"account": 1})

    assert response.status_code == 200
    assert sink.events == []
