"""Endpoint tests for the folder hierarchy and folder-aware file listing.

Builds a TestClient with the real app but overrides auth and the DB-bound
repositories with an in-memory SQLite engine.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.services.file_service import FileService
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import get_file_service


@dataclass
class FakeUser:
    id: int


class SqliteBackend:
    """Lazily creates the SQLite engine inside the app's event loop.

    Using NullPool + a file-based database avoids the loop-binding and
    connection-leak issues that plague :memory: SQLite inside TestClient.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._factory: async_sessionmaker | None = None

    async def ensure(self) -> async_sessionmaker:
        if self._factory is None:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}",
                poolclass=NullPool,
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                session.add(
                    UserModel(
                        id=1,
                        username="folder-user",
                        email="folder@example.com",
                        hashed_password="x",
                        is_active=True,
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            self._factory = factory
        return self._factory


@contextmanager
def folder_client(db_path: str) -> Generator[TestClient, None, None]:
    backend = SqliteBackend(db_path)

    async def no_op_initialize_database() -> None:
        return None

    class FakeStorageOps:
        async def stat_object(self, object_key: str) -> dict | None:
            del object_key
            return None

        async def remove_object(self, object_key: str) -> bool:
            del object_key
            return True

    class FakeSubscriptionRepo:
        async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
            del user_id
            return SubscriptionTier.FREE

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=FakeStorageOps(),
                subscription_repository=FakeSubscriptionRepo(),
            )

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=1)
    api_main.app.dependency_overrides[get_file_service] = override_file_service

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def test_create_and_list_root_folder(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        response = client.post("/api/v1/files/folders", json={"name": "Documents"})
        assert response.status_code == 201
        folder = response.json()
        assert folder["name"] == "Documents"
        assert folder["parent_id"] is None

        listing = client.get("/api/v1/files/folders").json()
        assert listing["total"] == 1
        assert listing["folders"][0]["name"] == "Documents"


def test_create_nested_folder(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        root = client.post("/api/v1/files/folders", json={"name": "Root"}).json()
        child = client.post(
            "/api/v1/files/folders", json={"name": "Child", "parent_id": root["id"]}
        )
        assert child.status_code == 201
        assert child.json()["parent_id"] == root["id"]


def test_create_folder_under_missing_parent_404(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        response = client.post(
            "/api/v1/files/folders", json={"name": "Orphan", "parent_id": "missing"}
        )
        assert response.status_code == 404


def test_folder_contents_lists_subfolders_and_files(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        root = client.post("/api/v1/files/folders", json={"name": "Root"}).json()
        client.post("/api/v1/files/folders", json={"name": "Sub", "parent_id": root["id"]})

        contents = client.get(f"/api/v1/files/folders/{root['id']}").json()
        assert contents["total_folders"] == 1
        assert contents["folders"][0]["name"] == "Sub"
        assert contents["total_files"] == 0


def test_rename_folder(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        folder = client.post("/api/v1/files/folders", json={"name": "Old"}).json()
        renamed = client.patch(f"/api/v1/files/folders/{folder['id']}", json={"name": "New"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "New"


def test_delete_folder_recursively(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        root = client.post("/api/v1/files/folders", json={"name": "Root"}).json()
        sub = client.post("/api/v1/files/folders", json={"name": "Sub", "parent_id": root["id"]}).json()

        assert client.delete(f"/api/v1/files/folders/{root['id']}").status_code == 204
        # both folders gone
        assert client.get(f"/api/v1/files/folders/{root['id']}").status_code == 404
        assert client.get(f"/api/v1/files/folders/{sub['id']}").status_code == 404


def test_unknown_folder_404(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        assert client.get("/api/v1/files/folders/missing").status_code == 404
        assert client.patch("/api/v1/files/folders/missing", json={"name": "x"}).status_code == 404
        assert client.delete("/api/v1/files/folders/missing").status_code == 404


def test_list_files_filters_by_folder(tmp_path) -> None:
    with folder_client(str(tmp_path / "folders.db")) as client:
        folder = client.post("/api/v1/files/folders", json={"name": "Docs"}).json()

        response = client.get("/api/v1/files", params={"folder_id": folder["id"]})
        assert response.status_code == 200
        assert response.json()["total"] == 0

        # unknown folder filter → 404
        assert client.get("/api/v1/files", params={"folder_id": "missing"}).status_code == 404
