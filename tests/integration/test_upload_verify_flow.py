"""Tests for the upload verify flow creating folder-aware file records."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFileModel, UserFolderModel, UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_service,
    get_file_service,
    get_transfer_service,
)


@dataclass
class FakeUser:
    id: int


class FakeSubscriptionRepo:
    """Returns a fixed tier for the upload size-limit check."""

    def __init__(self, tier: SubscriptionTier = SubscriptionTier.FREE) -> None:
        self._tier = tier

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        del user_id
        return self._tier


class FakeTransferService:
    def __init__(self, session: UploadSession | None = None) -> None:
        self._session = session

    async def create_upload(self, file_extension: str, user_id: str, **kwargs) -> UploadResponse:
        del file_extension, user_id, kwargs
        raise AssertionError("not used in this test")

    async def verify_upload_completion(
        self, upload_id: str, parts=None
    ) -> UploadSession:
        # ``parts`` is accepted (and ignored) so this fake matches the real
        # service signature after multipart finalization was added.
        del parts
        if self._session is not None:
            return self._session
        return UploadSession(
            upload_id=upload_id,
            object_key="uploads/abc123.pdf",
            status="completed",
            file_name="report.pdf",
            folder_id=None,
        )


class FakeConversionService:
    def __init__(self, job: ConversionJob | None = None) -> None:
        self._job = job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        del job_id
        return self._job

    async def push_conversion_job(self, job: ConversionJob) -> str:
        del job
        return ""

    async def update_conversion_job(self, job: ConversionJob) -> None:
        del job


class FakeStorage:
    """Object-storage stub whose reported size is mutable per test."""

    def __init__(self, size: int = 4096, content_type: str = "application/pdf") -> None:
        self.size = size
        self.content_type = content_type
        self.removed: list[str] = []

    async def stat_object(self, object_key: str) -> dict:
        del object_key
        return {"size": self.size, "content_type": self.content_type}

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        del object_key, max_bytes
        return b""

    async def remove_object(self, object_key: str) -> bool:
        self.removed.append(object_key)
        return True


class FakeUrlGateway:
    """Records multipart calls made by the real ``TransferService``."""

    def __init__(self) -> None:
        self.objects: set[str] = set()
        self.created: list[str] = []
        self.completed: list[tuple[str, str, list[tuple[int, str]]]] = []
        self.aborted: list[tuple[str, str]] = []

    def generate_put_url(self, object_key: str) -> str:
        return f"https://storage.test/put/{object_key}"

    def generate_get_url(self, object_key: str, expires_in_minutes: int) -> str:
        return f"https://storage.test/get/{object_key}"

    async def object_exists(self, object_key: str) -> bool:
        return object_key in self.objects

    def create_multipart_upload(self, object_key: str) -> str:
        self.created.append(object_key)
        return f"provider-multipart-{len(self.created)}"

    def generate_part_upload_url(
        self, object_key: str, part_number: int, upload_id: str, expires_in_minutes: int,
    ) -> str:
        return f"https://storage.test/part/{upload_id}/{part_number}?ttl={expires_in_minutes}"

    async def complete_multipart_upload(
        self, object_key: str, upload_id: str, parts: list[tuple[int, str]],
    ) -> None:
        self.completed.append((object_key, upload_id, list(parts)))
        # Completing an upload makes the assembled object appear in the bucket.
        self.objects.add(object_key)

    async def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        self.aborted.append((object_key, upload_id))


class FakeSessionCache:
    """Session cache stub. Deleting a key simulates expiry."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, data: str, ttl) -> None:
        self.data[key] = data

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class SqliteBackend:
    """Lazily creates the SQLite engine inside the app's event loop."""

    def __init__(
        self,
        db_path: str,
        folder_id: str | None = None,
        seed_file_sizes: list[int] | None = None,
    ) -> None:
        self._db_path = db_path
        self._folder_id = folder_id
        self._seed_file_sizes = seed_file_sizes or []
        self._factory: async_sessionmaker | None = None

    async def ensure(self) -> async_sessionmaker:
        if self._factory is None:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}", poolclass=NullPool
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                session.add(
                    UserModel(
                        id=1,
                        username="upload-user",
                        email="upload@example.com",
                        hashed_password="x",
                        is_active=True,
                        created_at=datetime.now(UTC),
                    )
                )
                if self._folder_id:
                    session.add(
                        UserFolderModel(
                            id=self._folder_id,
                            user_id=1,
                            parent_id=None,
                            name="Docs",
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                    )
                # Pre-existing files, used to occupy part of the storage quota so
                # the quota (rather than the per-file cap) becomes the binding
                # constraint.
                for index, size in enumerate(self._seed_file_sizes):
                    session.add(
                        UserFileModel(
                            id=f"seed-{index}",
                            user_id=1,
                            folder_id=None,
                            file_key=f"files/seed-{index}.bin",
                            file_name=f"seed-{index}.bin",
                            file_extension="bin",
                            file_size_bytes=size,
                            mime_type="application/octet-stream",
                            created_at=datetime.now(UTC),
                        )
                    )
                await session.commit()
            self._factory = factory
        return self._factory


@contextmanager
def verify_client(
    db_path: str,
    folder_id: str | None = None,
    transfer: FakeTransferService | None = None,
    conversion: FakeConversionService | None = None,
) -> Generator[tuple[TestClient, SqliteBackend], None, None]:
    backend = SqliteBackend(db_path, folder_id=folder_id)

    async def no_op() -> None:
        return None

    async def override_transfer():
        return transfer or FakeTransferService()

    async def override_conversion():
        return conversion or FakeConversionService()

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=FakeStorage(),
                subscription_repository=FakeSubscriptionRepo(tier=SubscriptionTier.FREE),
            )

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=1)
    api_main.app.dependency_overrides[get_transfer_service] = override_transfer
    api_main.app.dependency_overrides[get_conversion_service] = override_conversion
    api_main.app.dependency_overrides[get_file_service] = override_file_service

    client = TestClient(api_main.app)
    try:
        yield client, backend
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def test_verify_creates_file_record_at_root(tmp_path) -> None:
    with verify_client(str(tmp_path / "verify.db")) as (client, backend):
        response = client.post("/api/uploads/sessions/sess-1/verify")
        assert response.status_code == 200
        assert response.json()["status"] == "completed"

        # The file record should appear in the root file listing.
        listing = client.get("/api/v1/files").json()
        assert listing["total"] == 1
        assert listing["files"][0]["file_name"] == "report.pdf"
        assert listing["files"][0]["file_size_bytes"] == 4096
        assert listing["files"][0]["mime_type"] == "application/pdf"
        assert listing["files"][0]["folder_id"] is None


def test_verify_creates_file_record_in_folder(tmp_path) -> None:
    folder_id = "folder-1"
    session = UploadSession(
        upload_id="sess-2",
        object_key="uploads/abc123.pdf",
        status="pending",
        file_name="report.pdf",
        folder_id=folder_id,
    )
    transfer = FakeTransferService(session=session)
    with verify_client(str(tmp_path / "verify.db"), folder_id=folder_id, transfer=transfer) as (client, backend):
        response = client.post("/api/uploads/sessions/sess-2/verify")
        assert response.status_code == 200

        contents = client.get(f"/api/v1/files/folders/{folder_id}").json()
        assert contents["total_files"] == 1
        assert contents["files"][0]["file_name"] == "report.pdf"
        assert contents["files"][0]["folder_id"] == folder_id


class OversizeStorage(FakeStorage):
    """Reports a file larger than the 5 GiB per-file cap."""

    def __init__(self) -> None:
        super().__init__(size=6 * 1024**3)


def test_verify_rejects_oversized_upload(tmp_path) -> None:
    """An upload beyond the tier's per-file cap must be rejected with 413.

    The response carries a structured ``detail`` (the SPA branches on ``code``)
    and the rejected object must not be left in the bucket.
    """
    session = UploadSession(
        upload_id="sess-3",
        object_key="uploads/huge.pdf",
        status="pending",
        file_name="huge.pdf",
        folder_id=None,
    )
    transfer = FakeTransferService(session=session)
    storage = OversizeStorage()

    backend = _make_plain_backend(str(tmp_path / "verify.db"))

    async def no_op() -> None:
        return None

    async def override_transfer():
        return transfer

    async def override_conversion():
        return FakeConversionService()

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=storage,
                subscription_repository=FakeSubscriptionRepo(tier=SubscriptionTier.FREE),
            )

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=1)
    api_main.app.dependency_overrides[get_transfer_service] = override_transfer
    api_main.app.dependency_overrides[get_conversion_service] = override_conversion
    api_main.app.dependency_overrides[get_file_service] = override_file_service

    client = TestClient(api_main.app)
    try:
        response = client.post("/api/uploads/sessions/sess-3/verify")
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert detail["code"] == "FILE_TOO_LARGE"
        assert detail["max_file_size_bytes"] == 5 * 1024**3
        assert detail["file_size"] == 6 * 1024**3
        assert "maximum size" in detail["message"].lower()
        # No orphan: a rejected upload must not keep occupying the bucket.
        assert storage.removed == ["uploads/huge.pdf"]
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


# ---------------------------------------------------------------------------
# Full stack: real FileService + real TransferService over SQLite + fakes
# ---------------------------------------------------------------------------

GB = 1024**3
# Small multipart numbers so the tests stay cheap; production uses 100 MiB /
# 64 MiB (see settings.MULTIPART_THRESHOLD_BYTES / _PART_SIZE_BYTES).
TEST_THRESHOLD = 100
TEST_PART_SIZE = 10


@dataclass
class UploadHarness:
    client: TestClient
    storage: FakeStorage
    gateway: FakeUrlGateway
    cache: FakeSessionCache


@contextmanager
def upload_client(
    db_path: str,
    *,
    storage_size: int = 4096,
    seed_file_sizes: list[int] | None = None,
    tier: SubscriptionTier = SubscriptionTier.FREE,
) -> Generator[UploadHarness, None, None]:
    """Wire the REAL services over SQLite with fake storage/gateway/cache.

    Unlike :func:`verify_client` (which stubs TransferService to isolate the
    verify endpoint), this exercises the whole upload path — router, file
    service and transfer service — against a stubbed object store, which is what
    makes the quota enforcement and multipart orchestration testable without a
    bucket.
    """
    backend = SqliteBackend(db_path, seed_file_sizes=seed_file_sizes)
    storage = FakeStorage(size=storage_size)
    gateway = FakeUrlGateway()
    cache = FakeSessionCache()
    transfer = TransferService(
        storage_port=gateway,
        cache_port=cache,
        ttl_minutes=15,
        large_ttl_minutes=120,
        multipart_threshold_bytes=TEST_THRESHOLD,
        multipart_part_size_bytes=TEST_PART_SIZE,
    )

    async def no_op() -> None:
        return None

    async def override_transfer():
        return transfer

    async def override_conversion():
        return FakeConversionService()

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=storage,
                subscription_repository=FakeSubscriptionRepo(tier=tier),
            )

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=1)
    api_main.app.dependency_overrides[get_transfer_service] = override_transfer
    api_main.app.dependency_overrides[get_conversion_service] = override_conversion
    api_main.app.dependency_overrides[get_file_service] = override_file_service

    client = TestClient(api_main.app)
    try:
        yield UploadHarness(client=client, storage=storage, gateway=gateway, cache=cache)
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def _create_session(harness: UploadHarness, **body):
    payload = {"file_extension": "pdf", "file_name": "big.pdf"}
    payload.update(body)
    return harness.client.post("/api/uploads/sessions", json=payload)


def _user_files(db_path: str) -> list[UserFileModel]:
    """Read the persisted file rows straight from SQLite."""
    async def _read() -> list[UserFileModel]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        try:
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                repo = SQLUserFileRepository(session=session)
                rows, _ = await repo.list_by_user(1, limit=100)
                return list(rows)
        finally:
            await engine.dispose()

    return asyncio.run(_read())


# ---------------------------------------------------------------------------
# Pre-flight (declared file_size at session creation)
# ---------------------------------------------------------------------------

def test_create_session_without_a_declared_size_is_unchanged(tmp_path) -> None:
    """A client that does not declare a size gets exactly the historical
    response: a single presigned PUT and no pre-check (even when the account is
    already at its quota — the authoritative check happens at finalize)."""
    db_path = str(tmp_path / "preflight.db")
    with upload_client(db_path, seed_file_sizes=[5 * GB]) as harness:
        response = _create_session(harness)

        assert response.status_code == 201
        body = response.json()
        assert body["upload_mode"] == "single"
        assert body["upload_url"] is not None
        assert body["part_count"] is None
        assert body["part_size_bytes"] is None
        assert body["max_file_size_bytes"] == 5 * GB


def test_create_session_rejects_a_declared_size_above_the_per_file_cap(tmp_path) -> None:
    """413 with the structured FILE_TOO_LARGE code and the numbers the SPA needs
    to explain the refusal — and no session created."""
    db_path = str(tmp_path / "cap.db")
    with upload_client(db_path) as harness:
        response = _create_session(harness, file_size=5 * GB + 1)

        assert response.status_code == 413
        detail = response.json()["detail"]
        assert detail == {
            "code": "FILE_TOO_LARGE",
            "message": detail["message"],
            "max_file_size_bytes": 5 * GB,
            "file_size": 5 * GB + 1,
        }
        assert harness.gateway.created == []


def test_create_session_rejects_a_declared_size_over_the_remaining_quota(tmp_path) -> None:
    """The per-file cap and the account quota are independent: this file is
    below the cap but does not fit in what is left (4 GiB used of 5 GiB)."""
    db_path = str(tmp_path / "quota.db")
    with upload_client(db_path, seed_file_sizes=[4 * GB]) as harness:
        response = _create_session(harness, file_size=2 * GB)

        assert response.status_code == 413
        detail = response.json()["detail"]
        assert detail["code"] == "STORAGE_QUOTA_EXCEEDED"
        assert detail["limit_bytes"] == 5 * GB
        assert detail["used_bytes"] == 4 * GB
        assert detail["available_bytes"] == 1 * GB
        assert detail["file_size"] == 2 * GB


def test_create_session_allows_a_file_that_exactly_fits_the_quota(tmp_path) -> None:
    """Boundary: used + declared == limit is allowed, not rejected."""
    db_path = str(tmp_path / "exact.db")
    with upload_client(db_path, seed_file_sizes=[4 * GB]) as harness:
        response = _create_session(harness, file_size=GB)

        assert response.status_code == 201


def test_create_session_rejects_a_non_positive_declared_size(tmp_path) -> None:
    """A zero/negative size is meaningless and must not silently parse as
    "no size declared" — 422 from the schema bound."""
    db_path = str(tmp_path / "bound.db")
    with upload_client(db_path) as harness:
        assert _create_session(harness, file_size=0).status_code == 422
        assert _create_session(harness, file_size=-1).status_code == 422


def test_create_session_below_the_threshold_stays_single_put(tmp_path) -> None:
    db_path = str(tmp_path / "small.db")
    with upload_client(db_path) as harness:
        body = _create_session(harness, file_size=TEST_THRESHOLD - 1).json()

        assert body["upload_mode"] == "single"
        assert body["upload_url"] is not None
        assert harness.gateway.created == []


def test_create_session_at_the_threshold_uses_multipart(tmp_path) -> None:
    """Boundary and happy path in one: at the threshold the session becomes a
    multipart session with a real provider upload id."""
    db_path = str(tmp_path / "multi.db")
    with upload_client(db_path) as harness:
        response = _create_session(harness, file_size=TEST_THRESHOLD)

        assert response.status_code == 201
        body = response.json()
        assert body["upload_mode"] == "multipart"
        assert body["upload_url"] is None
        assert body["part_size_bytes"] == TEST_PART_SIZE
        assert body["part_count"] == TEST_THRESHOLD // TEST_PART_SIZE
        # Minted with the long window: a multi-gigabyte transfer outlives 15 min.
        assert body["expires_in_minutes"] == 120
        assert harness.gateway.created == [body["object_key"]]


# ---------------------------------------------------------------------------
# Part URLs
# ---------------------------------------------------------------------------

def _multipart_session(harness: UploadHarness, declared: int = TEST_THRESHOLD) -> dict:
    response = _create_session(harness, file_size=declared)
    assert response.status_code == 201, response.text
    return response.json()


def test_parts_endpoint_mints_urls_for_a_multipart_session(tmp_path) -> None:
    db_path = str(tmp_path / "parts.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/parts",
            json={"part_numbers": [2, 1]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["expires_in_minutes"] == 120
        assert [part["part_number"] for part in body["parts"]] == [1, 2]
        assert all(part["url"].startswith("https://") for part in body["parts"])


def test_parts_endpoint_rejects_a_single_put_session(tmp_path) -> None:
    db_path = str(tmp_path / "parts-single.db")
    with upload_client(db_path) as harness:
        session = _create_session(harness, file_size=TEST_THRESHOLD - 1).json()

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/parts",
            json={"part_numbers": [1]},
        )

        assert response.status_code == 409


def test_parts_endpoint_on_an_unknown_session_is_gone(tmp_path) -> None:
    """410, not 404: the cache cannot tell "never existed" from "expired", and
    the recovery is the same (start a new session)."""
    db_path = str(tmp_path / "parts-missing.db")
    with upload_client(db_path) as harness:
        response = harness.client.post(
            "/api/uploads/sessions/does-not-exist/parts", json={"part_numbers": [1]}
        )

        assert response.status_code == 410


def test_parts_endpoint_on_an_expired_session_is_gone(tmp_path) -> None:
    db_path = str(tmp_path / "parts-expired.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)
        harness.cache.data.pop(session["upload_id"])  # simulate TTL expiry

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/parts",
            json={"part_numbers": [1]},
        )

        assert response.status_code == 410


def test_parts_endpoint_rejects_an_out_of_range_part_number(tmp_path) -> None:
    db_path = str(tmp_path / "parts-range.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)  # part_count == 10

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/parts",
            json={"part_numbers": [11]},
        )

        assert response.status_code == 422


def test_parts_endpoint_caps_a_batch_at_one_hundred(tmp_path) -> None:
    """The per-call batch limit is enforced by the schema, so a client asking
    for 101 URLs gets a validation error rather than an unbounded response."""
    db_path = str(tmp_path / "parts-cap.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/parts",
            json={"part_numbers": list(range(1, 102))},
        )

        assert response.status_code == 422


def test_delete_aborts_a_multipart_upload(tmp_path) -> None:
    db_path = str(tmp_path / "delete-multi.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)

        response = harness.client.delete(f"/api/uploads/sessions/{session['upload_id']}")

        assert response.status_code == 204
        assert harness.gateway.aborted == [
            (session["object_key"], "provider-multipart-1")
        ]


def test_verify_completes_a_multipart_upload_and_persists_the_file(tmp_path) -> None:
    db_path = str(tmp_path / "verify-multi.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)

        response = harness.client.post(
            f"/api/uploads/sessions/{session['upload_id']}/verify",
            json={"parts": [{"part_number": 2, "etag": '"b"'}, {"part_number": 1, "etag": '"a"'}]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        # The parts reach the provider in ascending order, which
        # CompleteMultipartUpload requires.
        assert harness.gateway.completed == [
            (session["object_key"], "provider-multipart-1", [(1, '"a"'), (2, '"b"')])
        ]
        rows = _user_files(db_path)
        assert len(rows) == 1
        assert rows[0].file_size_bytes == 4096


def test_verify_of_a_multipart_session_without_parts_is_422(tmp_path) -> None:
    db_path = str(tmp_path / "verify-multi-body.db")
    with upload_client(db_path) as harness:
        session = _multipart_session(harness)

        response = harness.client.post(f"/api/uploads/sessions/{session['upload_id']}/verify")

        assert response.status_code == 422
        assert harness.gateway.completed == []


# ---------------------------------------------------------------------------
# Authoritative enforcement at finalize
# ---------------------------------------------------------------------------

def test_verify_rejects_when_the_file_would_exceed_the_quota(tmp_path) -> None:
    """Used (4 GiB) + real size (2 GiB) > 5 GiB quota: 413 with an accurate
    ``available_bytes``, no file row, and no object left behind."""
    db_path = str(tmp_path / "finalize-quota.db")
    with upload_client(
        db_path, storage_size=2 * GB, seed_file_sizes=[4 * GB]
    ) as harness:
        session = _create_session(harness, file_size=1).json()
        # The bytes really are in the bucket: only the DECLARED size was small.
        harness.gateway.objects.add(session["object_key"])

        response = harness.client.post(f"/api/uploads/sessions/{session['upload_id']}/verify")

        assert response.status_code == 413
        detail = response.json()["detail"]
        assert detail["code"] == "STORAGE_QUOTA_EXCEEDED"
        assert detail["limit_bytes"] == 5 * GB
        assert detail["used_bytes"] == 4 * GB
        assert detail["available_bytes"] == 1 * GB
        assert detail["file_size"] == 2 * GB

        # No orphan row for the rejected upload...
        assert [row.id for row in _user_files(db_path)] == ["seed-0"]
        # ...and no orphan object.
        assert harness.storage.removed == [session["object_key"]]


def test_finalize_rejects_a_client_that_lied_about_the_size(tmp_path) -> None:
    """THE lying-client case, and the reason the pre-flight check is only
    advisory.

    The client declares one byte, which passes the pre-flight check (available:
    1 GiB), then uploads 2 GiB. Only the finalize check — which MEASURES the
    object rather than trusting the declaration — can catch this; without it the
    account would silently end up over quota.
    """
    db_path = str(tmp_path / "liar.db")
    with upload_client(
        db_path, storage_size=2 * GB, seed_file_sizes=[4 * GB]
    ) as harness:
        created = _create_session(harness, file_size=1)
        assert created.status_code == 201, "the declaration alone must look fine"
        harness.gateway.objects.add(created.json()["object_key"])

        response = harness.client.post(
            f"/api/uploads/sessions/{created.json()['upload_id']}/verify"
        )

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "STORAGE_QUOTA_EXCEEDED"
        # Nothing was committed for the lie.
        assert [row.id for row in _user_files(db_path)] == ["seed-0"]


def test_finalize_rejects_a_client_that_lied_about_a_huge_size(tmp_path) -> None:
    """Same lie, but the measured size also breaks the per-file cap, which is
    checked first: the response says FILE_TOO_LARGE."""
    db_path = str(tmp_path / "liar-cap.db")
    with upload_client(db_path, storage_size=6 * GB) as harness:
        created = _create_session(harness, file_size=1)
        assert created.status_code == 201
        harness.gateway.objects.add(created.json()["object_key"])

        response = harness.client.post(
            f"/api/uploads/sessions/{created.json()['upload_id']}/verify"
        )

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "FILE_TOO_LARGE"
        assert _user_files(db_path) == []


def test_a_normal_small_upload_still_succeeds(tmp_path) -> None:
    """The unchanged path: create (no declared size) → PUT → verify → one row."""
    db_path = str(tmp_path / "normal.db")
    with upload_client(db_path) as harness:
        session = _create_session(harness).json()
        harness.gateway.objects.add(session["object_key"])  # the client's PUT

        response = harness.client.post(f"/api/uploads/sessions/{session['upload_id']}/verify")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        rows = _user_files(db_path)
        assert len(rows) == 1
        assert rows[0].file_key == session["object_key"]
        assert rows[0].file_name == "big.pdf"


def test_verifying_twice_does_not_double_count_against_the_quota(tmp_path) -> None:
    """A retry of a successful upload must not be rejected by the quota check:
    the first commit already counted these bytes (idempotency short-circuit)."""
    db_path = str(tmp_path / "retry.db")
    with upload_client(db_path, storage_size=5 * GB) as harness:
        session = _create_session(harness).json()
        harness.gateway.objects.add(session["object_key"])

        first = harness.client.post(f"/api/uploads/sessions/{session['upload_id']}/verify")
        second = harness.client.post(f"/api/uploads/sessions/{session['upload_id']}/verify")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(_user_files(db_path)) == 1


def _make_plain_backend(db_path: str) -> SqliteBackend:
    return SqliteBackend(db_path)


def _job(job_id: str, user_id: int | None) -> ConversionJob:
    from src.domain.conversions.value_object.conversion_type import ConversionType

    return ConversionJob(
        job_id=job_id,
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file="in.pdf",
        object_key="uploads/original.pdf",
        user_id=user_id,
    )


def test_verify_rejects_repointing_another_users_job(tmp_path) -> None:
    """SEC-2: the ``job_id`` query param must not mutate a job the caller
    does not own (IDOR write)."""
    foreign_job = _job("job-foreign", user_id=999)
    with verify_client(
        str(tmp_path / "verify.db"),
        conversion=FakeConversionService(job=foreign_job),
    ) as (client, _backend):
        response = client.post("/api/uploads/sessions/sess-1/verify?job_id=job-foreign")

        assert response.status_code == 404
        # The foreign job must not have been re-pointed at this session's object.
        assert foreign_job.object_key == "uploads/original.pdf"


def test_verify_repoints_the_callers_own_job(tmp_path) -> None:
    """The legitimate same-user flow (SPA ``verifyUpload(upload_id, job_id)``)
    must keep working."""
    own_job = _job("job-mine", user_id=1)
    with verify_client(
        str(tmp_path / "verify.db"),
        conversion=FakeConversionService(job=own_job),
    ) as (client, _backend):
        response = client.post("/api/uploads/sessions/sess-1/verify?job_id=job-mine")

        assert response.status_code == 200
        assert own_job.object_key == "uploads/abc123.pdf"


def test_verify_twice_creates_only_one_file_record(tmp_path) -> None:
    """QUAL-1: a repeated verify (double click / client retry) must not insert a
    second ``user_files`` row for the same object key."""
    with verify_client(str(tmp_path / "verify.db")) as (client, _backend):
        first = client.post("/api/uploads/sessions/sess-1/verify")
        second = client.post("/api/uploads/sessions/sess-1/verify")

        assert first.status_code == 200
        assert second.status_code == 200

        listing = client.get("/api/v1/files").json()
        assert listing["total"] == 1
