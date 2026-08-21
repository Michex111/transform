"""Tests for the upload verify flow creating folder-aware file records."""

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
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFolderModel, UserModel
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

    async def verify_upload_completion(self, upload_id: str) -> UploadSession:
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
    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        del job_id
        return None

    async def push_conversion_job(self, job: ConversionJob) -> str:
        del job
        return ""

    async def update_conversion_job(self, job: ConversionJob) -> None:
        del job


class FakeStorage:
    async def stat_object(self, object_key: str) -> dict:
        del object_key
        return {"size": 4096, "content_type": "application/pdf"}

    async def remove_object(self, object_key: str) -> bool:
        del object_key
        return True


class SqliteBackend:
    """Lazily creates the SQLite engine inside the app's event loop."""

    def __init__(self, db_path: str, folder_id: str | None = None) -> None:
        self._db_path = db_path
        self._folder_id = folder_id
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
                await session.commit()
            self._factory = factory
        return self._factory


@contextmanager
def verify_client(
    db_path: str, folder_id: str | None = None, transfer: FakeTransferService | None = None
) -> Generator[tuple[TestClient, SqliteBackend], None, None]:
    backend = SqliteBackend(db_path, folder_id=folder_id)

    async def no_op() -> None:
        return None

    async def override_transfer():
        return transfer or FakeTransferService()

    async def override_conversion():
        return FakeConversionService()

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
    """Reports a file far larger than the FREE tier limit (100 MB)."""

    async def stat_object(self, object_key: str) -> dict:
        del object_key
        return {"size": 500 * 1024 * 1024, "content_type": "application/pdf"}


def test_verify_rejects_oversized_upload(tmp_path) -> None:
    """An upload beyond the tier's size limit must be rejected with 413."""
    session = UploadSession(
        upload_id="sess-3",
        object_key="uploads/huge.pdf",
        status="pending",
        file_name="huge.pdf",
        folder_id=None,
    )
    transfer = FakeTransferService(session=session)

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
                storage=OversizeStorage(),
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
        assert "maximum size" in response.json()["detail"].lower()
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def _make_plain_backend(db_path: str) -> SqliteBackend:
    return SqliteBackend(db_path)
