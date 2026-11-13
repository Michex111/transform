"""The MCP attachment endpoint: streamed cap, pipeline, CORS, limits.

The endpoint is the only place a capability turns into bytes, so these tests
pin the properties an attacker would probe: the body is cut off at
``max_bytes + 1`` (streamed, not from a declared length), a refused upload still
marks the intent FAILED and cleans up its session, and every refusal that
concerns the capability is the one generic 404.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.file_system_exceptions import (
    FileSizeLimitExceededError,
    FileTypeMismatchError,
    StorageQuotaExceededError,
)
from src.domain.security.enitities.mcp_upload_intent import (
    MCPUploadIntent,
    MCPUploadIntentStatus,
)
from src.infrastructure.config.settings import get_settings
from src.presentation.api.dependencies.mcp_upload import MCPUploadEndpointServices
from src.presentation.api.middleware.body_limit import RequestBodyLimitMiddleware
from src.presentation.api.middleware.mcp_upload_cors import MCPUploadCORSMiddleware
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from src.presentation.api.routers.v1.mcp_upload import (
    UPLOAD_HEADER_NAME,
    router,
)

PATH = "/api/v1/mcp/upload"
CAPABILITY = "a-capability-value"


def _intent(*, max_bytes: int = 1024) -> MCPUploadIntent:
    now = datetime.now(UTC)
    return MCPUploadIntent(
        id="intent-1",
        user_id=7,
        grant_id="grant-1",
        client_id="client-1",
        file_name="report.pdf",
        extension="pdf",
        max_bytes=max_bytes,
        folder_id=None,
        status=MCPUploadIntentStatus.PENDING,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )


class FakeCapabilities:
    def __init__(self, intent: MCPUploadIntent) -> None:
        self.intent = intent
        self.failed: list[tuple[str, str]] = []
        self.completed: list[tuple[str, str]] = []

    async def redeem_start(self, capability: str) -> MCPUploadIntent:
        if capability != CAPABILITY:
            from src.application.services.mcp.upload_capability import UploadUnavailable

            raise UploadUnavailable()
        self.intent.status = MCPUploadIntentStatus.RECEIVING
        return self.intent

    async def inspect(self, capability: str) -> MCPUploadIntent:
        return await self.redeem_start(capability)

    async def fail(self, intent_id: str, error: str) -> None:
        self.failed.append((intent_id, error))
        self.intent.status = MCPUploadIntentStatus.FAILED
        self.intent.error = error

    async def complete(self, intent: MCPUploadIntent, file_id: str) -> MCPUploadIntent:
        self.completed.append((intent.id, file_id))
        self.intent.status = MCPUploadIntentStatus.COMPLETED
        self.intent.file_id = file_id
        return self.intent


class FakeTransfers:
    def __init__(self, *, threshold: int = 1024, part_size: int = 16) -> None:
        self.threshold = threshold
        self.part_size = part_size
        self.sessions: dict[str, UploadResponse] = {}
        self.verified: list[tuple[str, list[tuple[int, str]] | None]] = []
        self.deleted: list[str] = []

    @property
    def multipart_threshold_bytes(self) -> int:
        return self.threshold

    async def create_upload(
        self,
        file_extension: str,
        user_id: str,
        file_name: str | None = None,
        folder_id: str | None = None,
        file_size: int | None = None,
    ) -> UploadResponse:
        del user_id, file_name, folder_id
        session_id = f"upload-{len(self.sessions) + 1}"
        multipart = file_size is not None and file_size >= self.threshold
        response = UploadResponse(
            upload_id=session_id,
            object_key=f"upload/{session_id}/file.{file_extension}",
            upload_url=None if multipart else "https://storage.test/put",
            expires_in_minutes=15,
            upload_mode="multipart" if multipart else "single",
            part_size_bytes=self.part_size if multipart else None,
            part_count=(-(-(file_size or 0) // self.part_size) if multipart else None),
        )
        self.sessions[session_id] = response
        return response

    async def get_upload_session(self, upload_id: str) -> UploadSession:
        response = self.sessions[upload_id]
        return UploadSession(
            upload_id=upload_id,
            object_key=response.object_key,
            file_extension="pdf",
            upload_mode=response.upload_mode,
            part_size_bytes=response.part_size_bytes,
            part_count=response.part_count,
            multipart_upload_id="mp-1" if response.upload_mode == "multipart" else None,
        )

    async def generate_part_urls(
        self, session: UploadSession, numbers: list[int]
    ) -> list[tuple[int, str]]:
        return [(number, f"https://storage.test/part/{number}") for number in numbers]

    async def verify_upload_completion(
        self, upload_id: str, parts: list[tuple[int, str]] | None = None
    ) -> UploadSession:
        self.verified.append((upload_id, parts))
        session = await self.get_upload_session(upload_id)
        session.status = "completed"
        return session

    async def delete_upload_session(self, upload_id: str) -> None:
        self.deleted.append(upload_id)


class FakeFiles:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.completed: list[tuple[int, str]] = []

    async def complete_upload(self, user_id: int, session: UploadSession) -> str:
        if self.error is not None:
            raise self.error
        self.completed.append((user_id, session.object_key))
        return "file-1"


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object(self, object_key: str, data: bytes) -> None:
        self.objects[object_key] = data


class Recorder:
    def __init__(self) -> None:
        self.parts: list[tuple[str, int]] = []

    async def __call__(self, url: str, data: bytes) -> str:
        self.parts.append((url, len(data)))
        return '"etag"'


def _app(
    *,
    intent: MCPUploadIntent | None = None,
    transfers: FakeTransfers | None = None,
    files: FakeFiles | None = None,
    store: FakeObjectStore | None = None,
    put_part: Any = None,
    cors: bool = False,
) -> tuple[TestClient, MCPUploadEndpointServices]:
    from src.presentation.api.dependencies.mcp_upload import (
        get_mcp_upload_endpoint_services,
    )

    capabilities = FakeCapabilities(intent or _intent())
    services = MCPUploadEndpointServices(
        capabilities=capabilities,  # type: ignore[arg-type]
        transfers=transfers or FakeTransfers(),  # type: ignore[arg-type]
        files=files or FakeFiles(),  # type: ignore[arg-type]
        object_store=store or FakeObjectStore(),  # type: ignore[arg-type]
        put_part=put_part or Recorder(),
    )
    app = FastAPI()
    app.include_router(router)
    if cors:
        app.add_middleware(MCPUploadCORSMiddleware)
    app.dependency_overrides[get_mcp_upload_endpoint_services] = lambda: services
    return TestClient(app), services


def test_a_streamed_body_over_the_cap_is_413_and_marks_failed() -> None:
    client, services = _app(intent=_intent(max_bytes=10))

    response = client.put(
        PATH, content=b"x" * 11, headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 413
    assert response.json()["ok"] is False
    capabilities = services.capabilities
    assert isinstance(capabilities, FakeCapabilities)
    assert capabilities.failed
    assert capabilities.intent.status is MCPUploadIntentStatus.FAILED
    transfers = services.transfers
    assert isinstance(transfers, FakeTransfers)
    assert transfers.sessions == {}


def test_a_successful_put_runs_the_normal_pipeline() -> None:
    store = FakeObjectStore()
    client, services = _app(store=store)

    response = client.put(
        PATH, content=b"hello", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "ok": True,
        "upload_id": "intent-1",
        "file_id": "file-1",
        "job_id": None,
        "status": "COMPLETED",
    }
    assert store.objects == {"upload/upload-1/file.pdf": b"hello"}
    files = services.files
    assert isinstance(files, FakeFiles)
    assert files.completed == [(7, "upload/upload-1/file.pdf")]


def test_a_body_at_or_above_the_threshold_uses_multipart_parts() -> None:
    transfers = FakeTransfers(threshold=16, part_size=16)
    recorder = Recorder()
    client, services = _app(transfers=transfers, put_part=recorder)

    body = b"y" * 100
    response = client.put(
        PATH, content=body, headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 200, response.text
    assert sum(size for _url, size in recorder.parts) == 100
    assert [url for url, _size in recorder.parts] == [
        "https://storage.test/part/1",
        "https://storage.test/part/2",
        "https://storage.test/part/3",
        "https://storage.test/part/4",
        "https://storage.test/part/5",
        "https://storage.test/part/6",
        "https://storage.test/part/7",
    ]
    assert transfers.verified
    assert transfers.verified[0][1] is not None
    assert len(transfers.verified[0][1] or []) == 7


def test_a_magic_byte_mismatch_marks_failed_and_cleans_up_the_session() -> None:
    files = FakeFiles(FileTypeMismatchError("Uploaded content does not match the claimed .pdf type."))
    client, services = _app(files=files)

    response = client.put(
        PATH, content=b"not a pdf", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code in (400, 422)
    assert "does not match" in response.json()["error"]
    transfers = services.transfers
    assert isinstance(transfers, FakeTransfers)
    assert transfers.deleted == ["upload-1"]
    capabilities = services.capabilities
    assert isinstance(capabilities, FakeCapabilities)
    assert capabilities.intent.status is MCPUploadIntentStatus.FAILED


def test_a_tier_cap_refusal_passes_the_file_service_message_through() -> None:
    """The sentence names the tier's cap, which is the actionable part."""
    files = FakeFiles(
        FileSizeLimitExceededError("File exceeds the maximum size for your tier (5 MB).")
    )
    client, services = _app(files=files)

    response = client.put(
        PATH, content=b"hello", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 413
    assert "maximum size for your tier" in response.json()["error"]
    transfers = services.transfers
    assert isinstance(transfers, FakeTransfers)
    assert transfers.deleted == ["upload-1"]
    capabilities = services.capabilities
    assert isinstance(capabilities, FakeCapabilities)
    assert capabilities.intent.status is MCPUploadIntentStatus.FAILED


def test_an_account_quota_refusal_passes_the_file_service_message_through() -> None:
    """The quota sentence is different from the per-file cap and must survive."""
    files = FakeFiles(
        StorageQuotaExceededError(
            "Not enough storage left for this file.",
            limit_bytes=100,
            used_bytes=100,
            available_bytes=0,
            file_size=5,
        )
    )
    client, services = _app(files=files)

    response = client.put(
        PATH, content=b"hello", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "Not enough storage left for this file."
    transfers = services.transfers
    assert isinstance(transfers, FakeTransfers)
    assert transfers.deleted == ["upload-1"]
    capabilities = services.capabilities
    assert isinstance(capabilities, FakeCapabilities)
    assert capabilities.intent.status is MCPUploadIntentStatus.FAILED


def test_a_missing_or_unknown_capability_is_the_generic_404() -> None:
    client, _services = _app()
    for headers in ({}, {UPLOAD_HEADER_NAME: "wrong"}):
        response = client.put(PATH, content=b"x", headers=headers)
        assert response.status_code == 404
        assert response.json()["detail"] == "This upload link is unavailable or has expired."


def test_a_multipart_form_post_uses_the_same_path() -> None:
    store = FakeObjectStore()
    client, _services = _app(store=store)
    response = client.post(
        PATH,
        files={"file": ("report.pdf", b"hello", "application/pdf")},
        headers={UPLOAD_HEADER_NAME: CAPABILITY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["file_id"] == "file-1"
    assert store.objects == {"upload/upload-1/file.pdf": b"hello"}


def test_the_info_endpoint_does_not_consume_the_link() -> None:
    client, services = _app(intent=_intent(max_bytes=2048))
    response = client.get(
        f"{PATH}/info", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 200
    assert response.json()["max_bytes"] == 2048
    assert response.json()["extension"] == "pdf"
    capabilities = services.capabilities
    assert isinstance(capabilities, FakeCapabilities)
    assert capabilities.intent.status is not MCPUploadIntentStatus.FAILED


def test_the_page_and_script_are_no_store_and_cors_headers_are_present() -> None:
    client, _services = _app(cors=True)
    page = client.get(PATH)
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["x-content-type-options"] == "nosniff"

    script = client.get(f"{PATH}/script.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]

    preflight = client.options(
        PATH,
        headers={
            "Origin": "https://sandbox.example.test",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": UPLOAD_HEADER_NAME,
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert UPLOAD_HEADER_NAME in preflight.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in preflight.headers


def test_cors_is_opened_only_on_the_upload_path() -> None:
    app = FastAPI()

    @app.get("/elsewhere")
    async def elsewhere() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(MCPUploadCORSMiddleware)
    client = TestClient(app)
    response = client.get("/elsewhere", headers={"Origin": "https://x.test"})
    assert "access-control-allow-origin" not in response.headers


def test_the_body_limit_uses_the_attachment_cap_for_this_path_only() -> None:
    middleware = RequestBodyLimitMiddleware(
        app=None,  # type: ignore[arg-type]
        max_bytes=100,
        mcp_max_bytes=200,
        attachment_max_bytes=1000,
    )
    assert middleware._cap_for(PATH) == 1000
    assert middleware._cap_for("/api/v1/files") == 100
    assert middleware._cap_for("/mcp") == 200
    assert middleware._cap_for("/health") is None


def test_the_rate_limit_key_is_the_capability_not_the_anonymous_ip_bucket() -> None:
    middleware = RateLimitMiddleware(app=None)  # type: ignore[arg-type]

    def request(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request(
            {
                "type": "http",
                "method": "PUT",
                "path": PATH,
                "headers": headers,
                "client": ("203.0.113.9", 1234),
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "root_path": "",
            }
        )

    key, limit = middleware._resolve_limit(
        request([(b"x-transform-upload", b"cap-1")])
    )
    assert key.startswith("mcp-upload:")
    assert limit == get_settings().MCP_UPLOAD_RATE_LIMIT
    assert "203.0.113.9" not in key

    fallback, _ = middleware._resolve_limit(request([]))
    assert fallback == "ip:203.0.113.9:mcp-upload"
    assert fallback != "ip:203.0.113.9"


def test_a_conversion_failure_is_reported_without_losing_the_upload() -> None:
    intent = _intent()

    class FailingCapabilities(FakeCapabilities):
        async def complete(
            self, intent: MCPUploadIntent, file_id: str
        ) -> MCPUploadIntent:
            self.intent.status = MCPUploadIntentStatus.FAILED
            self.intent.file_id = file_id
            self.intent.error = "The conversion could not be started."
            return self.intent

    from src.presentation.api.dependencies.mcp_upload import (
        get_mcp_upload_endpoint_services,
    )

    services = MCPUploadEndpointServices(
        capabilities=FailingCapabilities(intent),  # type: ignore[arg-type]
        transfers=FakeTransfers(),  # type: ignore[arg-type]
        files=FakeFiles(),  # type: ignore[arg-type]
        object_store=FakeObjectStore(),  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_mcp_upload_endpoint_services] = lambda: services
    client = TestClient(app)

    response = client.put(
        PATH, content=b"hello", headers={UPLOAD_HEADER_NAME: CAPABILITY}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["file_id"] == "file-1"
    assert payload["status"] == "FAILED"
    assert payload["error"] == "The conversion could not be started."


def test_the_endpoint_never_reads_identity_from_the_request() -> None:
    """A user id in a header or query must change nothing about the intent."""
    client, _services = _app()
    response = client.put(
        PATH
        + "?user_id=999&object_key=upload/evil&bucket=other",
        content=b"hello",
        headers={
            UPLOAD_HEADER_NAME: CAPABILITY,
            "X-User-Id": "999",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["upload_id"] == "intent-1"
