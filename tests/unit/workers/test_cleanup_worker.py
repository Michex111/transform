"""Tests for the cleanup worker (guest data retention)."""

import asyncio
import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import workers.cleanup_worker.main as cleanup_main
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
    """Insert a conversion-job row the way the API producer does.

    IMPORTANT: the producer stores the bare *display* filename in
    ``input_file`` and the real object-store key in ``object_key`` (see
    ``conversion_service`` + ``ConversionJobModel``). Writing the key into
    ``input_file`` here used to hide the bug where the cleanup worker deleted
    the display name instead of the object, so nothing was ever removed.
    """
    async with factory() as session:
        session.add(
            ConversionJobModel(
                job_id=job_id,
                status=JobStatus.COMPLETED,
                source_format="pdf",
                target_format="docx",
                input_file=Path(input_key).name,
                object_key=input_key,
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
    # A short interval by default so tests never sleep; callers may override.
    kwargs.setdefault("cleanup_interval", 3600)
    return CleanupWorker(
        storage=storage,
        db_session_factory=factory,
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
            # The real object key is deleted, not the display filename that the
            # producer put in ``input_file``.
            assert "guest/old-in.pdf" in storage.removed
            assert "guest/old-out.docx" in storage.removed
            assert "old-in.pdf" not in storage.removed
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
            assert "old-in.pdf" not in storage.removed
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


def test_cleanup_worker_info_logs_are_visible_under_production_configuration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """W-10: the cleanup worker's INFO logs must not be discarded.

    Production configures a handler on the *named* ``file_converter_worker``
    logger only, leaving the root logger at WARNING. The cleanup module logs
    through ``logging.getLogger(__name__)``, so every INFO line — including the
    per-cycle counts — was dropped and a worker failing on every cycle looked
    identical to a healthy one.
    """
    # Emulate production: root stays at WARNING. A logger whose effective level
    # is WARNING never even creates the record.
    caplog.set_level(logging.WARNING)
    # ...but do capture INFO records if they are created, so the assertion below
    # fails on the *level gate* rather than on the capture handler's threshold.
    caplog.handler.setLevel(logging.INFO)

    cleanup_main.configure_logging()

    logging.getLogger("workers.cleanup_worker.worker").info("cleanup cycle summary")

    assert any(
        record.levelno == logging.INFO and record.getMessage() == "cleanup cycle summary"
        for record in caplog.records
    )


def test_cleanup_stop_wakes_the_inter_cycle_sleep() -> None:
    """W-2: ``stop()`` must not have to wait out a multi-hour interval.

    The loop used to ``await asyncio.sleep(interval)`` with no way to
    interrupt, so a SIGTERM handler calling ``stop()`` would hang for the whole
    interval (6 hours by default).
    """
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            storage = FakeCleanupStorage()
            worker = _make_worker(storage, factory, cleanup_interval=6 * 60 * 60)

            task = asyncio.create_task(worker.run())
            # Let the first cycle run and the loop reach its wait.
            await asyncio.sleep(0)
            worker.stop()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(_run())


def test_cleanup_startup_endpoint_hides_credentials(monkeypatch) -> None:
    """W-13 for the cleanup worker: log the endpoint, never the credentials.

    The cleanup worker deletes real user data and has no Redis client, so the
    equivalent mismatch risk is which *database* it points at.
    """

    class FakeSecret:
        @staticmethod
        def get_secret_value() -> str:
            return "postgresql+asyncpg://transform:sup3rs3cret@db.example:5432/transform"

    class FakeSettings:
        DATABASE_URL = FakeSecret()

    monkeypatch.setattr(cleanup_main, "get_settings", lambda: FakeSettings())

    endpoint = cleanup_main._redacted_database_endpoint()

    assert endpoint == "db.example:5432/transform"
    assert "sup3rs3cret" not in endpoint
    assert "transform:" not in endpoint

