"""Tests for the cleanup worker (guest data retention)."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.database.models import (
    ConversionJobModel,
    UserFileModel,
    UserModel,
)
from src.infrastructure.database.session import Base
from workers.cleanup_worker.worker import CleanupWorker


class FakeCleanupStorage:
    """In-memory object storage recording removals."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.removed: list[str] = []

    def remove_object(self, key: str) -> bool:
        if key in self.objects:
            del self.objects[key]
        self.removed.append(key)
        return True

    def list_objects(self, prefix: str) -> list[dict]:
        now = datetime.now(UTC)
        return [
            {
                "object_name": key,
                "size": len(value),
                "last_modified": now,
            }
            for key, value in self.objects.items()
            if key.startswith(prefix)
        ]


@contextmanager
def sqlite_session_factory():
    """Yields an async_sessionmaker bound to a fresh in-memory SQLite DB."""

    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _add_user(factory) -> UserModel:
    async with factory() as session:
        user = UserModel(
            username="cleanup-user",
            email="cleanup@example.com",
            hashed_password="x",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _add_job(factory, *, job_id: str, user_id: int | None, age_hours: float, input_key: str, output_key: str | None) -> None:
    async with factory() as session:
        session.add(
            ConversionJobModel(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                source_format="pdf",
                target_format="docx",
                input_file=input_key,
                output_file=output_key,
                user_id=user_id,
                created_at=datetime.now(UTC) - timedelta(hours=age_hours),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _add_file(factory, *, file_id: str, user_id: int, age_hours: float, expires_hours_ago: float | None, file_key: str) -> None:
    async with factory() as session:
        session.add(
            UserFileModel(
                id=file_id,
                user_id=user_id,
                file_key=file_key,
                file_name=f"{file_id}.pdf",
                file_size_bytes=10,
                mime_type="application/pdf",
                created_at=datetime.now(UTC) - timedelta(hours=age_hours),
                expires_at=(
                    datetime.now(UTC) - timedelta(hours=expires_hours_ago)
                    if expires_hours_ago is not None
                    else None
                ),
            )
        )
        await session.commit()


def _make_worker(storage, factory, **kwargs) -> CleanupWorker:
    return CleanupWorker(
        storage=storage,
        db_session_factory=factory,
        cleanup_interval=3600,
        **kwargs,
    )


def test_cleanup_guest_jobs_removes_old_ownerless_jobs_and_objects() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            storage = FakeCleanupStorage(
                {
                    "guest/old-in.pdf": b"old",
                    "guest/old-out.docx": b"old-out",
                    "guest/fresh-in.pdf": b"fresh",
                }
            )
            await _add_job(
                factory, job_id="old-guest", user_id=None, age_hours=48,
                input_key="guest/old-in.pdf", output_key="guest/old-out.docx",
            )
            await _add_job(
                factory, job_id="fresh-guest", user_id=None, age_hours=2,
                input_key="guest/fresh-in.pdf", output_key=None,
            )

            worker = _make_worker(storage, factory, guest_job_retention_hours=24)
            cleaned = await worker._cleanup_guest_jobs()

            assert cleaned == 1
            assert "guest/old-in.pdf" in storage.removed
            assert "guest/old-out.docx" in storage.removed
            assert "guest/fresh-in.pdf" not in storage.removed

            async with factory() as session:
                assert await session.get(ConversionJobModel, "old-guest") is None
                assert await session.get(ConversionJobModel, "fresh-guest") is not None

        asyncio.run(_run())


def test_cleanup_guest_jobs_keeps_authenticated_jobs() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _add_user(factory)
            storage = FakeCleanupStorage({"user/old-in.pdf": b"x"})
            await _add_job(
                factory, job_id="owned-old", user_id=user.id, age_hours=48,
                input_key="user/old-in.pdf", output_key=None,
            )

            worker = _make_worker(storage, factory, guest_job_retention_hours=24)
            cleaned = await worker._cleanup_guest_jobs()

            assert cleaned == 0
            async with factory() as session:
                assert await session.get(ConversionJobModel, "owned-old") is not None

        asyncio.run(_run())


def test_cleanup_expired_files_removes_only_past_expiry() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _add_user(factory)
            storage = FakeCleanupStorage(
                {
                    "guest/expired.pdf": b"e",
                    "guest/active.pdf": b"a",
                }
            )
            await _add_file(
                factory, file_id="expired", user_id=user.id, age_hours=48,
                expires_hours_ago=2, file_key="guest/expired.pdf",
            )
            await _add_file(
                factory, file_id="active", user_id=user.id, age_hours=2,
                expires_hours_ago=-48,  # expires in the future
                file_key="guest/active.pdf",
            )

            worker = _make_worker(storage, factory, guest_file_retention_hours=24)
            cleaned = await worker._cleanup_expired_files()

            assert cleaned == 1
            assert "guest/expired.pdf" in storage.removed
            assert "guest/active.pdf" not in storage.removed

            async with factory() as session:
                assert await session.get(UserFileModel, "expired") is None
                assert await session.get(UserFileModel, "active") is not None

        asyncio.run(_run())


def test_cleanup_temp_files_removes_stale_temp_objects() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            storage = FakeCleanupStorage(
                {
                    "temp/stale.bin": b"s",
                    "temp/fresh.bin": b"f",
                    "output/keep.pdf": b"k",
                }
            )
            # Force the stale object's last_modified far in the past
            stale_key = "temp/stale.bin"
            storage.objects[stale_key] = b"s"
            original = storage.list_objects

            def list_with_stale(prefix):
                items = original(prefix)
                for item in items:
                    if item["object_name"] == stale_key:
                        item["last_modified"] = datetime.now(UTC) - timedelta(hours=5)
                return items

            storage.list_objects = list_with_stale

            worker = _make_worker(storage, factory, temp_file_retention_hours=1)
            cleaned = await worker._cleanup_temp_files()

            assert cleaned == 1
            assert stale_key in storage.removed
            assert "temp/fresh.bin" not in storage.removed
            assert "output/keep.pdf" not in storage.removed

        asyncio.run(_run())


def test_archive_old_jobs_removes_history_and_objects() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _add_user(factory)
            storage = FakeCleanupStorage(
                {
                    "user/old-in.pdf": b"o",
                    "user/old-out.pdf": b"o",
                    "user/new-in.pdf": b"n",
                }
            )
            await _add_job(
                factory, job_id="old-job", user_id=user.id, age_hours=24 * 40,
                input_key="user/old-in.pdf", output_key="user/old-out.pdf",
            )
            await _add_job(
                factory, job_id="new-job", user_id=user.id, age_hours=24 * 5,
                input_key="user/new-in.pdf", output_key=None,
            )

            worker = _make_worker(storage, factory, job_archive_days=30)
            archived = await worker._archive_old_jobs()

            assert archived == 1
            assert "user/old-in.pdf" in storage.removed
            assert "user/old-out.pdf" in storage.removed
            async with factory() as session:
                assert await session.get(ConversionJobModel, "old-job") is None
                assert await session.get(ConversionJobModel, "new-job") is not None

        asyncio.run(_run())


def test_cleanup_cycle_runs_all_tasks() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            user = await _add_user(factory)
            storage = FakeCleanupStorage(
                {
                    "guest/old-in.pdf": b"g",
                    "user/expired.pdf": b"e",
                    "temp/stale.bin": b"t",
                }
            )
            await _add_job(
                factory, job_id="guest-old", user_id=None, age_hours=48,
                input_key="guest/old-in.pdf", output_key=None,
            )
            await _add_file(
                factory, file_id="expired-file", user_id=user.id, age_hours=48,
                expires_hours_ago=2, file_key="user/expired.pdf",
            )
            # Force stale temp object
            storage.objects["temp/stale.bin"] = b"t"
            original = storage.list_objects

            def list_with_stale(prefix):
                items = original(prefix)
                for item in items:
                    if item["object_name"] == "temp/stale.bin":
                        item["last_modified"] = datetime.now(UTC) - timedelta(hours=5)
                return items

            storage.list_objects = list_with_stale

            worker = _make_worker(storage, factory)
            await worker._run_cleanup_cycle()

            assert "guest/old-in.pdf" in storage.removed
            assert "user/expired.pdf" in storage.removed
            assert "temp/stale.bin" in storage.removed

        asyncio.run(_run())


def test_cleanup_tolerates_missing_objects() -> None:
    """Removing objects that no longer exist must not fail the cycle."""
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            storage = FakeCleanupStorage()  # empty — object already gone
            await _add_job(
                factory, job_id="orphan", user_id=None, age_hours=48,
                input_key="gone/input.pdf", output_key="gone/output.pdf",
            )

            worker = _make_worker(storage, factory, guest_job_retention_hours=24)
            cleaned = await worker._cleanup_guest_jobs()

            assert cleaned == 1
            assert "gone/input.pdf" in storage.removed
            assert "gone/output.pdf" in storage.removed
            async with factory() as session:
                assert await session.get(ConversionJobModel, "orphan") is None

        asyncio.run(_run())
