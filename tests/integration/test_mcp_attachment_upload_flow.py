"""End-to-end MCP attachment upload over the real transport.

This is the flow the feature exists for: an agent mints an upload link, the
client's sandbox PUTs the real bytes with the capability header, the server
stores them through the ordinary upload pipeline, and ``get_upload_status``
reports both the Drive file and the conversion job the link asked for.

Everything runs against the real app (real SQLite, real OAuth, real MCP SDK)
with only storage and the job queue replaced, so the authorization, folder and
quota rules under test are the production ones.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import src.presentation.api.main as api_main
import src.presentation.mcp.dependencies as mcp_deps
from src.infrastructure.database.models import ConversionJobModel, MCPUploadIntentModel
from src.presentation.api.dependencies.service_dependencies import (
    get_minio_download_adapter,
    get_minio_url_storage,
    get_session_cache,
)
from test_mcp_oauth_flow import MCPEnv, _authorize_and_redeem, mcp_env  # type: ignore[missing-import]

#: A minimal, real PDF. The magic-byte check only needs the header, but the file
#: is a genuine one so the test would still pass if a converter ran on it.
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\nstartxref\n0\n%%EOF\n"
UPLOAD_PATH = "/api/v1/mcp/upload"
HEADER = "X-Transform-Upload"


def _run(coro):
    return asyncio.run(coro)


class MemoryStorage:
    """Stand-in for ``MinioUrlStorageAdapter``, shared with the object store."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.removed: list[str] = []
        self.multipart_started: list[str] = []

    def generate_put_url(self, object_key: str) -> str:
        return f"https://storage.test/put/{object_key}"

    def generate_get_url(self, object_key: str, expires_in_minutes: int = 60) -> str:
        del expires_in_minutes
        return f"https://storage.test/get/{object_key}"

    def create_multipart_upload(self, object_key: str) -> str:
        self.multipart_started.append(object_key)
        return "mp-1"

    def generate_part_upload_url(
        self, object_key: str, part_number: int, upload_id: str, expires_in_minutes: int
    ) -> str:
        del object_key, upload_id, expires_in_minutes
        return f"https://storage.test/part/{part_number}"

    async def complete_multipart_upload(self, object_key, upload_id, parts) -> None:
        del object_key, upload_id, parts

    async def abort_multipart_upload(self, object_key, upload_id) -> None:
        del object_key, upload_id

    async def object_exists(self, object_key: str) -> bool:
        return object_key in self.objects

    async def stat_object(self, object_key: str) -> dict | None:
        data = self.objects.get(object_key)
        if data is None:
            return None
        return {"size": len(data), "content_type": "application/pdf"}

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        return (self.objects.get(object_key) or b"")[:max_bytes]

    async def remove_object(self, object_key: str) -> bool:
        self.removed.append(object_key)
        return self.objects.pop(object_key, None) is not None


class MemoryObjectStore:
    def __init__(self, storage: MemoryStorage) -> None:
        self.storage = storage

    async def put_object(self, object_key: str, data: bytes) -> None:
        self.storage.objects[object_key] = data


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, ttl=None) -> None:
        del ttl
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeQueue:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish_job(self, job, stream: str | None = None) -> None:
        del stream
        self.published.append(job.job_id)


_original_queue: object | None = None


def _install_overrides(env: MCPEnv, storage: MemoryStorage, cache: MemoryCache):
    global _original_queue
    queue = FakeQueue()
    api_main.app.dependency_overrides[get_minio_url_storage] = lambda: storage
    api_main.app.dependency_overrides[get_session_cache] = lambda: cache
    api_main.app.dependency_overrides[get_minio_download_adapter] = lambda: MemoryObjectStore(
        storage
    )
    _original_queue = mcp_deps.get_job_queue_port
    mcp_deps.get_job_queue_port = lambda: queue  # type: ignore[assignment]
    return queue


def _restore_overrides(_env: MCPEnv | None = None) -> None:
    global _original_queue
    if _original_queue is not None:
        mcp_deps.get_job_queue_port = _original_queue  # type: ignore[assignment]
        _original_queue = None


def _write_scope(env: MCPEnv) -> tuple[dict, str]:
    env.seed_user()
    return _authorize_and_redeem(
        env,
        requested="documents.read documents.convert documents.write",
        approved=["documents.read", "documents.convert", "documents.write"],
    )


def test_a_sandbox_put_lands_the_file_and_starts_the_conversion(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        queue = _install_overrides(env, storage, cache)
        try:
            _client, token = _write_scope(env)

            link = env.call_tool(
                token,
                "create_upload_link",
                {"file_name": "sample.pdf", "target_format": "docx"},
            )
            assert link["ok"] is True, link
            assert link["upload_header_name"] == HEADER
            assert link["curl_command"].startswith("curl --fail -sS -X PUT ")
            assert "#" in link["upload_page_url"]
            # The capability is never in the query string the tool returns.
            assert "?" not in link["upload_url"]
            # No OAuth token or storage location is leaked into the result.
            serialized = str(link)
            assert token not in serialized
            assert "upload/" not in serialized

            response = env.client.put(
                UPLOAD_PATH,
                content=PDF,
                headers={HEADER: link["upload_capability"]},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["ok"] is True
            assert body["file_id"]
            assert body["job_id"]
            assert body["status"] == "COMPLETED"

            status = env.call_tool(
                token, "get_upload_status", {"upload_id": link["upload_id"]}
            )
            assert status["ok"] is True, status
            assert status["status"] == "COMPLETED"
            assert status["file"]["file_id"] == body["file_id"]
            assert status["job"]["job_id"] == body["job_id"]
            assert token not in str(status)
            assert "file_key" not in str(status)
            assert "object_key" not in str(status)

            async def read_job() -> ConversionJobModel | None:
                async with mcp_deps.get_session_factory()() as session:
                    return await session.get(ConversionJobModel, body["job_id"])

            row = _run(read_job())
            assert row is not None
            # The conversion is attributed to the agent and to this grant, which
            # is what makes the history scope and the audit trail exact.
            assert row.origin == "MCP"
            assert row.grant_id
            assert queue.published == [body["job_id"]]

            # Replaying the capability is the same generic 404 as a bad one.
            replay = env.client.put(
                UPLOAD_PATH,
                content=PDF,
                headers={HEADER: link["upload_capability"]},
            )
            assert replay.status_code == 404
            assert replay.json()["detail"] == "This upload link is unavailable or has expired."
        finally:
            _restore_overrides(env)


def test_a_browser_multipart_post_works(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        _install_overrides(env, storage, cache)
        try:
            _client, token = _write_scope(env)
            link = env.call_tool(
                token, "create_upload_link", {"file_name": "sample.pdf"}
            )
            assert link["ok"] is True, link

            response = env.client.post(
                UPLOAD_PATH,
                files={"file": ("sample.pdf", PDF, "application/pdf")},
                headers={HEADER: link["upload_capability"]},
            )
            assert response.status_code == 200, response.text
            assert response.json()["file_id"]
            assert response.json()["status"] == "COMPLETED"
        finally:
            _restore_overrides(env)


def test_an_expired_link_is_the_generic_404(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        _install_overrides(env, storage, cache)
        try:
            _client, token = _write_scope(env)
            link = env.call_tool(
                token, "create_upload_link", {"file_name": "sample.pdf"}
            )
            assert link["ok"] is True, link

            async def expire() -> None:
                async with mcp_deps.get_session_factory()() as session:
                    row = await session.get(MCPUploadIntentModel, link["upload_id"])
                    assert row is not None
                    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                    await session.commit()

            _run(expire())
            response = env.client.put(
                UPLOAD_PATH,
                content=PDF,
                headers={HEADER: link["upload_capability"]},
            )
            assert response.status_code == 404
            assert response.json()["detail"] == "This upload link is unavailable or has expired."
            assert storage.objects == {}
        finally:
            _restore_overrides(env)


def test_a_link_needs_the_write_permission(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        _install_overrides(env, storage, cache)
        try:
            env.seed_user()
            _client, token = _authorize_and_redeem(env)  # read + convert only
            refused = env.call_tool(
                token, "create_upload_link", {"file_name": "sample.pdf"}
            )
            assert refused["ok"] is False
            assert refused["required_scope"] == "documents.write"
            assert storage.objects == {}
        finally:
            _restore_overrides(env)


def test_url_import_is_refused_when_disabled(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        _install_overrides(env, storage, cache)
        try:
            _client, token = _write_scope(env)
            refused = env.call_tool(
                token,
                "import_file_from_url",
                {"url": "https://example.com/report.pdf"},
            )
            assert refused["ok"] is False
            assert refused["error"] == "URL import is not enabled on this server."
        finally:
            _restore_overrides(env)


def test_upload_status_is_scoped_to_the_uploading_connection(tmp_path) -> None:
    storage = MemoryStorage()
    cache = MemoryCache()
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        _install_overrides(env, storage, cache)
        try:
            _client, ada_token = _write_scope(env)
            env.seed_user(username="bob", email="bob@example.com")
            link = env.call_tool(
                ada_token, "create_upload_link", {"file_name": "sample.pdf"}
            )
            assert link["ok"] is True
            env.client.put(
                UPLOAD_PATH,
                content=PDF,
                headers={HEADER: link["upload_capability"]},
            )

            # Another user cannot see it, and the answer is indistinguishable
            # from a nonexistent upload.
            _, bob_token = _authorize_and_redeem(
                env,
                username="bob",
                requested="documents.read documents.convert documents.write",
                approved=["documents.read", "documents.convert", "documents.write"],
            )
            foreign = env.call_tool(
                bob_token, "get_upload_status", {"upload_id": link["upload_id"]}
            )
            missing = env.call_tool(
                bob_token, "get_upload_status", {"upload_id": "does-not-exist"}
            )
            assert foreign == missing
            assert foreign["ok"] is False
        finally:
            _restore_overrides(env)
