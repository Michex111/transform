"""Endpoint tests for the dashboard ``storage_stats.breakdown`` field.

Builds a ``TestClient`` with the real app, overriding auth and the DB session
with an in-memory/file-backed SQLite engine (the established pattern in this
suite). Files are seeded directly through ``SQLUserFileRepository``.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.auth_dependencies import get_current_user

USER_ID = 1

# (file_name, file_extension, file_size_bytes)
FILES = [
    ("a.pdf", "pdf", 5_242_880),           # 5 MiB
    ("b.PDF", ".pdf", 5_242_880),          # 5 MiB, normalised from "*.PDF"
    ("c.png", "png", 7_340_032),           # 7 MiB
    ("README", "", 1_024),                 # no extension
    ("archive.tar.bz2", "tar.bz2", 2_048),  # multi-dot name
    ("empty.jpg", "jpg", 0),               # zero-byte -> excluded
]


@dataclass
class FakeUser:
    id: int


def _seed(db_path: str, files: list[tuple[str, str, int]]) -> None:
    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                UserModel(
                    id=USER_ID,
                    username="dash-user",
                    email="dash@example.com",
                    hashed_password="x",
                    is_active=True,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            repo = SQLUserFileRepository(session)
            for file_name, extension, size in files:
                await repo.save(
                    user_id=USER_ID,
                    file_key=f"files/{file_name}",
                    file_name=file_name,
                    file_size_bytes=size,
                    mime_type="application/octet-stream",
                    file_extension=extension,
                )
        await engine.dispose()

    asyncio.run(_run())


@contextmanager
def dashboard_client(
    db_path: str, *, files: list[tuple[str, str, int]] = FILES
) -> Generator[TestClient, None, None]:
    _seed(db_path, files)

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


def test_dashboard_storage_breakdown_is_correctly_shaped_and_sorted(tmp_path) -> None:
    with dashboard_client(str(tmp_path / "dash.db")) as client:
        response = client.get("/api/v1/user/dashboard")
        assert response.status_code == 200, response.text
        stats = response.json()["storage_stats"]

        # Pre-existing fields must be unchanged and still present.
        assert stats["used_bytes"] == 5_242_880 + 5_242_880 + 7_340_032 + 1_024 + 2_048
        assert stats["file_count"] == 6  # zero-byte file still counted
        assert "limit_bytes" in stats
        assert "used_percent" in stats

        # New field: sorted by bytes DESC, only entries with bytes > 0.
        assert stats["breakdown"] == [
            {"extension": "pdf", "bytes": 10_485_760, "file_count": 2},
            {"extension": "png", "bytes": 7_340_032, "file_count": 1},
            {"extension": "tar.bz2", "bytes": 2_048, "file_count": 1},
            {"extension": "", "bytes": 1_024, "file_count": 1},
        ]

        # Every entry exposes exactly the frozen contract keys.
        for entry in stats["breakdown"]:
            assert set(entry) == {"extension", "bytes", "file_count"}
            assert isinstance(entry["extension"], str)
            assert entry["extension"] == entry["extension"].lower()
            assert not entry["extension"].startswith(".")
            assert entry["bytes"] > 0


def test_dashboard_storage_breakdown_is_empty_without_files(tmp_path) -> None:
    with dashboard_client(str(tmp_path / "empty.db"), files=[]) as client:
        response = client.get("/api/v1/user/dashboard")
        assert response.status_code == 200, response.text
        stats = response.json()["storage_stats"]
        assert stats["breakdown"] == []
        assert stats["used_bytes"] == 0
        assert stats["file_count"] == 0


def test_dashboard_exposes_the_limits_the_upload_path_enforces(tmp_path) -> None:
    """The SPA renders its upload pre-check from these, so they must agree with
    the numbers the server enforces: ``available_bytes`` is the real headroom
    and ``max_file_size_bytes`` is the per-tier per-file cap (5 GiB)."""
    from src.domain.subscriptions.policies.tier_policy import TierPolicy
    from src.domain.subscriptions.value_object.tier import SubscriptionTier

    with dashboard_client(str(tmp_path / "limits.db")) as client:
        response = client.get("/api/v1/user/dashboard")
        assert response.status_code == 200, response.text
        stats = response.json()["storage_stats"]

        quota = TierPolicy.for_tier(SubscriptionTier.FREE).storage_quota_bytes
        assert stats["limit_bytes"] == quota
        assert stats["available_bytes"] == quota - stats["used_bytes"]
        assert stats["available_bytes"] == max(0, quota - stats["used_bytes"])
        assert stats["max_file_size_bytes"] == 5 * 1024**3
        # Independent controls: the per-file cap equals the FREE quota here, but
        # they are separate settings and must not be assumed equal.
        assert stats["max_file_size_bytes"] <= stats["limit_bytes"]


def test_dashboard_available_bytes_never_goes_negative(tmp_path) -> None:
    """An account over quota must report 0 headroom, not a negative number."""
    over_quota = [
        ("huge.bin", "bin", 6 * 1024**3),  # more than the 5 GiB FREE quota
    ]
    with dashboard_client(str(tmp_path / "over.db"), files=over_quota) as client:
        stats = client.get("/api/v1/user/dashboard").json()["storage_stats"]

        assert stats["used_bytes"] > stats["limit_bytes"]
        assert stats["available_bytes"] == 0
