"""Endpoint tests for the guest (unauthenticated) conversion surface.

Guests are identified only by a minted ``guest_token``; a ``job_id`` alone must
NOT grant access. These tests cover token gating (missing/incorrect token on
status / events / download), guest job creation returning a token, the verify
step enqueuing at the GUEST tier, the guest size limit, and both the redirect
(no encryption) and streaming (encryption) download paths.
"""

import io
import json
from contextlib import contextmanager
from datetime import timedelta
from typing import Generator

from fastapi.testclient import TestClient

import src.presentation.api.main as api_main
from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_conversion_service,
    get_encryption_service,
    get_event_subscriber,
    get_guest_token_cache,
    get_minio_download_adapter,
    get_minio_url_storage,
    get_transfer_service,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeGuestTokenCache:
    """In-memory stand-in for the Redis-backed guest-token store."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, data: str, ttl: timedelta) -> None:
        del ttl
        self._store[key] = data

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class FakeConversionService:
    def __init__(self) -> None:
        self.created_jobs: list[ConversionJob] = []
        self.pushed: list[tuple[ConversionJob, SubscriptionTier]] = []

    async def create_conversion_job(self, job: ConversionJob) -> str:
        job.job_id = "test-job-id"
        self.created_jobs.append(job)
        return job.job_id

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        for job in self.created_jobs:
            if job.job_id == job_id:
                return job
        return None

    async def push_conversion_job(
        self,
        job: ConversionJob,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> str:
        self.pushed.append((job, tier))
        if job.status.value == "AWAITING_UPLOAD":
            job.pending_processing()
        return job.job_id

    async def update_conversion_job(self, job: ConversionJob) -> None:
        del job


class FakeTransferService:
    def __init__(self, session: UploadSession | None = None) -> None:
        self._session = session
        self.create_calls: list[tuple[str, str, str | None]] = []

    async def create_upload(
        self,
        file_extension: str,
        user_id: str,
        file_name: str | None = None,
        folder_id: str | None = None,
    ) -> UploadResponse:
        del folder_id
        self.create_calls.append((file_extension, user_id, file_name))
        return UploadResponse(
            upload_id="guest-upload-id",
            object_key="uploads/guest-example.docx",
            upload_url="https://storage.test/put-url",
            expires_in_minutes=10,
        )

    async def verify_upload_completion(self, upload_id: str) -> UploadSession:
        if self._session is not None:
            return self._session
        return UploadSession(
            upload_id=upload_id,
            object_key="uploads/guest-example.docx",
            status="completed",
            file_name="guest-example.docx",
            folder_id=None,
            user_id="guest",
        )

    async def create_download_url(
        self, object_key: str, *, expires_in_minutes: int | None = None
    ) -> str:
        del expires_in_minutes
        return f"https://storage.test/get/{object_key}"


class FakeUrlStorage:
    """Stand-in for MinioUrlStorageAdapter used by the transfer service."""

    def __init__(self, size: int = 4096) -> None:
        self.size = size

    def generate_put_url(self, object_key: str) -> str:
        del object_key
        return "https://storage.test/put-url"

    def generate_get_url(self, object_key: str, expires_in_minutes: int = 60) -> str:
        del expires_in_minutes
        return f"https://storage.test/get/{object_key}"

    async def object_exists(self, object_key: str) -> bool:
        del object_key
        return True

    async def stat_object(self, object_key: str) -> dict | None:
        del object_key
        return {"size": self.size, "content_type": "application/pdf"}


class FakeJobRepository:
    def __init__(self, job: ConversionJob) -> None:
        self.job = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        del job_id
        return self.job


class FakeSubscriber:
    def __init__(self, fields: dict) -> None:
        self.fields = fields

    async def iter_events(self, job_id: str):
        del job_id
        yield ("1", self.fields)


class FakeStreamResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self.closed = True


class FakeStreamingStorage(MinioFileStorageAdapter):
    def __init__(self, payload: bytes):
        super().__init__(bucket_name="test", s3_client=None)  # type: ignore[arg-type]
        self._payload = payload

    def get_object_stream(self, key: str):
        del key
        return FakeStreamResponse(self._payload)


def _completed_job(*, output_file: str = "output/guest/example.mov") -> ConversionJob:
    return ConversionJob(
        job_id="test-job-id",
        conversion=ConversionType("pdf", "docx"),
        input_file="uploads/guest-example.docx",
        output_file=output_file,
        object_key="uploads/guest-example.docx",
        status=JobStatus.COMPLETED,
    )


# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------

@contextmanager
def create_guest_test_client() -> Generator[TestClient, None, None]:
    async def no_op() -> None:
        return None

    conversion_service = FakeConversionService()
    transfer_service = FakeTransferService()
    cache = FakeGuestTokenCache()
    encryption = FileEncryptionService(allow_autogenerated_key=True)

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op
    api_main.app.dependency_overrides[get_conversion_service] = lambda: conversion_service
    api_main.app.dependency_overrides[get_transfer_service] = lambda: transfer_service
    api_main.app.dependency_overrides[get_guest_token_cache] = lambda: cache
    api_main.app.dependency_overrides[get_encryption_service] = lambda: encryption
    api_main.app.dependency_overrides[get_minio_url_storage] = lambda: FakeUrlStorage()

    client = TestClient(api_main.app)
    client.app.state.fake_conversion_service = conversion_service  # type: ignore[attr-defined]
    client.app.state.fake_transfer_service = transfer_service  # type: ignore[attr-defined]
    client.app.state.fake_guest_token_cache = cache  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        for attr in (
            "fake_conversion_service",
            "fake_transfer_service",
            "fake_guest_token_cache",
        ):
            if hasattr(api_main.app.state, attr):
                delattr(api_main.app.state, attr)


def _create_guest_job(client: TestClient) -> dict:
    """Create a guest job and return {job_id, guest_token}."""
    response = client.post(
        "/api/guest/conversions/jobs",
        json={
            "source_format": "pdf",
            "target_format": "docx",
            "input_key": "uploads/guest-example.docx",
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_guest_supported_map_requires_no_token() -> None:
    with create_guest_test_client() as client:
        response = client.get("/api/guest/conversions/supported/map")
    assert response.status_code == 200
    conversions = response.json()["conversions"]
    assert "pdf" in conversions
    assert "docx" in conversions["pdf"]


def test_guest_create_job_returns_token_and_awaits_upload() -> None:
    with create_guest_test_client() as client:
        payload = _create_guest_job(client)

    assert payload["job_id"] == "test-job-id"
    assert payload["status"] == "AWAITING_UPLOAD"
    assert payload["guest_token"]
    assert payload["source_format"] == "pdf"
    assert payload["target_format"] == "docx"
    assert payload["object_key"] is None


def test_guest_create_job_with_client_encryption_wraps_data_key() -> None:
    """A guest client-encrypted upload wraps the key under the fixed 'guest' actor."""
    import base64

    from src.infrastructure.adapters.security.encryption import FileEncryptionService

    raw_key = bytes([0x42]) * 32

    with create_guest_test_client() as client:
        encryption = FileEncryptionService(allow_autogenerated_key=True)
        client.app.dependency_overrides[get_encryption_service] = lambda: encryption #type: ignore

        response = client.post(
            "/api/guest/conversions/jobs",
            json={
                "source_format": "pdf",
                "target_format": "docx",
                "input_key": "uploads/guest-example.docx",
                "client_encrypted": True,
                "data_key": base64.b64encode(raw_key).decode(),
            },
        )
        payload = response.json()
        conversion_service = client.app.state.fake_conversion_service  # type: ignore[attr-defined]
        job = conversion_service.created_jobs[0]

    assert response.status_code == 202
    assert payload["client_encrypted"] is True
    assert payload["data_key_wrapped"] == job.data_key_wrapped
    assert job.data_key_wrapped != raw_key.hex()
    # Guest jobs wrap the key under the "guest" actor (user_id is None).
    assert encryption.decrypt_file(bytes.fromhex(job.data_key_wrapped), "guest") == raw_key


def test_guest_create_upload_session() -> None:
    with create_guest_test_client() as client:
        response = client.post(
            "/api/guest/uploads/sessions",
            json={"file_extension": "docx", "file_name": "guest-example.docx"},
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["upload_id"] == "guest-upload-id"
    assert payload["object_key"] == "uploads/guest-example.docx"
    assert "upload_url" in payload


def test_guest_verify_requires_token_missing_returns_401() -> None:
    with create_guest_test_client() as client:
        _create_guest_job(client)
        response = client.post("/api/guest/uploads/sessions/upload-1/verify?job_id=test-job-id")
    assert response.status_code == 401


def test_guest_verify_rejects_incorrect_token_returns_403() -> None:
    with create_guest_test_client() as client:
        _create_guest_job(client)
        response = client.post(
            "/api/guest/uploads/sessions/upload-1/verify",
            params={"job_id": "test-job-id", "guest_token": "wrong-token"},
        )
    assert response.status_code == 403


def test_guest_verify_enqueues_with_guest_tier() -> None:
    with create_guest_test_client() as client:
        payload = _create_guest_job(client)
        token = payload["guest_token"]
        conversion_service = client.app.state.fake_conversion_service  # type: ignore[attr-defined]

        response = client.post(
            "/api/guest/uploads/sessions/upload-1/verify",
            params={"job_id": payload["job_id"], "guest_token": token},
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"

        # The job was enqueued at the GUEST tier and transitioned to PENDING.
        assert len(conversion_service.pushed) == 1
        pushed_job, pushed_tier = conversion_service.pushed[0]
        assert pushed_tier == SubscriptionTier.GUEST
        assert pushed_job.object_key == "uploads/guest-example.docx"
        assert pushed_job.status == JobStatus.PENDING


def test_guest_verify_rejects_oversized_file_returns_413() -> None:
    # Use a URL storage adapter that reports a file far larger than the guest limit.
    async def no_op() -> None:
        return None

    conversion_service = FakeConversionService()
    transfer_service = FakeTransferService()
    cache = FakeGuestTokenCache()
    original_init = api_main.initialize_database
    api_main.initialize_database = no_op
    api_main.app.dependency_overrides[get_conversion_service] = lambda: conversion_service
    api_main.app.dependency_overrides[get_transfer_service] = lambda: transfer_service
    api_main.app.dependency_overrides[get_guest_token_cache] = lambda: cache
    api_main.app.dependency_overrides[get_minio_url_storage] = lambda: FakeUrlStorage(
        size=500 * 1024 * 1024
    )

    with TestClient(api_main.app) as client:
        job = client.post(
            "/api/guest/conversions/jobs",
            json={"source_format": "pdf", "target_format": "docx", "input_key": "k"},
        ).json()
        response = client.post(
            "/api/guest/uploads/sessions/upload-1/verify",
            params={"job_id": job["job_id"], "guest_token": job["guest_token"]},
        )

    assert response.status_code == 413
    api_main.app.dependency_overrides.clear()
    api_main.initialize_database = original_init


def test_guest_status_requires_token_missing_returns_401() -> None:
    with create_guest_test_client() as client:
        _create_guest_job(client)
        response = client.get("/api/guest/conversions/jobs/test-job-id")
    assert response.status_code == 401


def test_guest_status_rejects_incorrect_token_returns_403() -> None:
    with create_guest_test_client() as client:
        _create_guest_job(client)
        response = client.get(
            "/api/guest/conversions/jobs/test-job-id?guest_token=wrong-token"
        )
    assert response.status_code == 403


def test_guest_get_job_returns_presigned_url_when_unencrypted() -> None:
    with create_guest_test_client() as client:
        async def no_encryption():
            return None

        client.app.dependency_overrides[get_encryption_service] = no_encryption  # type: ignore[attr-defined]
        client.app.dependency_overrides[get_conversion_repository] = (  # type: ignore[attr-defined]
            lambda: FakeJobRepository(_completed_job())
        )
        _job = client.post(
            "/api/guest/conversions/jobs",
            json={"source_format": "pdf", "target_format": "docx", "input_key": "k"},
        ).json()
        response = client.get(
            "/api/guest/conversions/jobs/test-job-id",
            params={"guest_token": _job["guest_token"]},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "COMPLETED"
    assert payload["download_url"] == "https://storage.test/get/output/guest/example.mov"


def test_guest_download_requires_token_missing_returns_401() -> None:
    with create_guest_test_client() as client:
        response = client.get("/api/guest/conversions/jobs/test-job-id/download")
    assert response.status_code == 401


def test_guest_download_rejects_incorrect_token_returns_403() -> None:
    with create_guest_test_client() as client:
        response = client.get(
            "/api/guest/conversions/jobs/test-job-id/download?guest_token=wrong-token"
        )
    assert response.status_code == 403


def test_guest_download_redirects_to_presigned_url_when_no_encryption() -> None:
    with create_guest_test_client() as client:
        async def no_encryption():
            return None

        client.app.dependency_overrides[get_encryption_service] = no_encryption  # type: ignore[attr-defined]
        client.app.dependency_overrides[get_conversion_repository] = (  # type: ignore[attr-defined]
            lambda: FakeJobRepository(_completed_job())
        )
        _job = client.post(
            "/api/guest/conversions/jobs",
            json={"source_format": "pdf", "target_format": "docx", "input_key": "k"},
        ).json()
        response = client.get(
            "/api/guest/conversions/jobs/test-job-id/download",
            params={"guest_token": _job["guest_token"]},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["location"] == "https://storage.test/get/output/guest/example.mov"


def test_guest_download_streams_decrypted_output_when_encryption_enabled() -> None:
    # Build ciphertext for the "guest" actor, then stream it back.
    encryption = FileEncryptionService(allow_autogenerated_key=True)
    plaintext = b"guest secret document" * 500
    ciphertext = encryption.encrypt_bytes(plaintext, "guest")

    with create_guest_test_client() as client:
        client.app.dependency_overrides[get_encryption_service] = lambda: encryption  # type: ignore[attr-defined]
        client.app.dependency_overrides[get_minio_download_adapter] = (  # type: ignore[attr-defined]
            lambda: FakeStreamingStorage(ciphertext)
        )
        client.app.dependency_overrides[get_conversion_repository] = (  # type: ignore[attr-defined]
            lambda: FakeJobRepository(_completed_job(output_file="output/guest/example.mov"))
        )
        _job = client.post(
            "/api/guest/conversions/jobs",
            json={"source_format": "pdf", "target_format": "docx", "input_key": "k"},
        ).json()
        response = client.get(
            "/api/guest/conversions/jobs/test-job-id/download",
            params={"guest_token": _job["guest_token"]},
        )
    assert response.status_code == 200
    assert response.content == plaintext
    assert response.headers["content-disposition"].startswith("attachment")


def test_guest_events_requires_token_missing_returns_401() -> None:
    with create_guest_test_client() as client:
        response = client.get("/api/guest/events/jobs/test-job-id")
    assert response.status_code == 401


def test_guest_events_rejects_incorrect_token_returns_403() -> None:
    with create_guest_test_client() as client:
        response = client.get(
            "/api/guest/events/jobs/test-job-id?guest_token=wrong-token"
        )
    assert response.status_code == 403


def test_guest_events_streams_connected_and_terminal_event() -> None:
    with create_guest_test_client() as client:
        client.app.dependency_overrides[get_conversion_repository] = (  # type: ignore[attr-defined]
            lambda: FakeJobRepository(_completed_job())
        )
        client.app.dependency_overrides[get_event_subscriber] = (  # type: ignore[attr-defined]
            lambda: FakeSubscriber(
                {
                    "job_id": "test-job-id",
                    "status": "COMPLETED",
                    "progress": "100",
                    "message": "done",
                }
            )
        )
        _job = client.post(
            "/api/guest/conversions/jobs",
            json={"source_format": "pdf", "target_format": "docx", "input_key": "k"},
        ).json()
        with client.stream(
            "GET",
            "/api/guest/events/jobs/test-job-id",
            params={"guest_token": _job["guest_token"]},
        ) as stream:
            lines = [line for line in stream.iter_lines() if line]

    assert "event: connected" in lines
    data_lines = [l for l in lines if l.startswith("data: ")]
    payloads = [json.loads(l[len("data: "):]) for l in data_lines]
    completed = [p for p in payloads if p.get("status") == "COMPLETED"]
    assert completed, "expected a COMPLETED event payload"
    assert completed[0]["progress"] == "100"
