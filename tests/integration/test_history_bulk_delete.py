"""Endpoint tests for bulk conversion-history deletion.

Uses the real conversions router, the real ``ConversionService`` and the real
SQL repository against a file-backed SQLite database, so the scoping and the
status filter are exercised as SQL rather than as a fake.

The jobs are seeded with explicit ``created_at`` values rather than by sleeping,
so "inside the window" is exact instead of timing-dependent.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.database.models import ConversionJobModel, UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware

PREVIEW = "/api/conversions/history/delete-preview"
DELETE_RANGE = "/api/conversions/history"

ME = 1
OTHER = 2

#: (job_id, owner, status, age) — deliberately covers both status classes, both
#: sides of the window boundary, and both owners.
SEED: tuple[tuple[str, int, JobStatus, timedelta], ...] = (
    ("recent-done", ME, JobStatus.COMPLETED, timedelta(hours=1)),
    ("recent-failed", ME, JobStatus.FAILED, timedelta(hours=2)),
    ("recent-pending", ME, JobStatus.PENDING, timedelta(hours=1)),
    ("recent-processing", ME, JobStatus.PROCESSING, timedelta(hours=1)),
    ("old-done", ME, JobStatus.COMPLETED, timedelta(days=3)),
    ("other-recent", OTHER, JobStatus.COMPLETED, timedelta(hours=1)),
)


@dataclass
class FakeUser:
    """Only ``id`` is read by these endpoints."""

    id: int


@dataclass
class _NoopQueue:
    """The history endpoints never touch the queue; this only satisfies wiring."""

    async def publish_job(self, job):  # pragma: no cover - never called
        raise AssertionError("the history endpoints must not publish jobs")


def _seed(db_path: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        now = datetime.now(UTC)
        async with factory() as session:
            for user_id, username in ((ME, "me"), (OTHER, "other")):
                session.add(
                    UserModel(
                        id=user_id,
                        username=username,
                        email=f"{username}@example.com",
                        hashed_password="x",
                        is_active=True,
                        created_at=now,
                    )
                )
            for job_id, owner, status, age in SEED:
                session.add(
                    ConversionJobModel(
                        job_id=job_id,
                        status=status,
                        source_format="pdf",
                        target_format="docx",
                        input_file="a.pdf",
                        user_id=owner,
                        created_at=now - age,
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(_run())


def remaining_job_ids(db_path: str) -> set[str]:
    async def _run() -> set[str]:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                rows = (await session.execute(select(ConversionJobModel.job_id))).scalars().all()
                return set(rows)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@contextmanager
def history_client(db_path: str) -> Generator[TestClient, None, None]:
    _seed(db_path)

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
    original_is_allowed = RateLimitMiddleware._is_allowed

    async def allow_all(self, key, limit, window=60):
        del self, key, limit, window
        return True

    api_main.initialize_database = no_op_initialize_database
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=ME)

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def test_a_missing_range_is_rejected(tmp_path) -> None:
    """A missing parameter must never be able to mean "delete everything"."""
    with history_client(str(tmp_path / "a.db")) as client:
        preview = client.get(PREVIEW)
        deleted = client.delete(DELETE_RANGE)
        survivors = remaining_job_ids(str(tmp_path / "a.db"))

    assert preview.status_code == 422, preview.text
    assert deleted.status_code == 422, deleted.text
    # Nothing was removed by the rejected calls.
    assert survivors == {job_id for job_id, *_ in SEED}


@pytest.mark.parametrize("unknown", ["90d", "last_week", "ALL", "", "everything"])
def test_an_unknown_range_is_rejected(tmp_path, unknown: str) -> None:
    with history_client(str(tmp_path / f"b-{unknown or 'empty'}.db")) as client:
        preview = client.get(PREVIEW, params={"range": unknown})
        deleted = client.delete(DELETE_RANGE, params={"range": unknown})

    assert preview.status_code == 422, preview.text
    assert deleted.status_code == 422, deleted.text


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_the_preview_reports_the_window_and_the_counts(tmp_path) -> None:
    with history_client(str(tmp_path / "c.db")) as client:
        response = client.get(PREVIEW, params={"range": "24h"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["range"] == "24h"
    assert payload["since"] is not None
    # Two finished jobs inside 24h; the two in-flight ones are kept.
    assert payload["count"] == 2
    assert payload["active_count"] == 2


def test_the_preview_is_read_only(tmp_path) -> None:
    db_path = str(tmp_path / "d.db")
    with history_client(db_path) as client:
        client.get(PREVIEW, params={"range": "all"})
        survivors = remaining_job_ids(db_path)

    assert survivors == {job_id for job_id, *_ in SEED}


def test_the_preview_for_all_reports_no_lower_bound(tmp_path) -> None:
    with history_client(str(tmp_path / "e.db")) as client:
        response = client.get(PREVIEW, params={"range": "all"})

    assert response.status_code == 200, response.text
    assert response.json()["since"] is None
    # Every one of this user's terminal jobs, at any age.
    assert response.json()["count"] == 3
    assert response.json()["active_count"] == 2


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_a_24h_delete_removes_only_finished_jobs_inside_the_window(tmp_path) -> None:
    db_path = str(tmp_path / "f.db")
    with history_client(db_path) as client:
        response = client.delete(DELETE_RANGE, params={"range": "24h"})
        survivors = remaining_job_ids(db_path)

    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 2
    assert response.json()["skipped_active"] == 2
    assert response.json()["range"] == "24h"

    assert survivors == {
        # Older than the window.
        "old-done",
        # Still running, so never deleted out from under a worker.
        "recent-pending",
        "recent-processing",
        # Belongs to another user.
        "other-recent",
    }


def test_the_preview_count_matches_what_the_delete_reports(tmp_path) -> None:
    """The number the user agrees to and the number that disappears must agree."""
    with history_client(str(tmp_path / "g.db")) as client:
        preview = client.get(PREVIEW, params={"range": "24h"})
        deleted = client.delete(DELETE_RANGE, params={"range": "24h"})

    assert preview.status_code == 200, preview.text
    assert deleted.status_code == 200, deleted.text
    assert preview.json()["count"] == deleted.json()["deleted_count"]
    assert preview.json()["active_count"] == deleted.json()["skipped_active"]


def test_an_all_delete_also_removes_jobs_older_than_a_day(tmp_path) -> None:
    db_path = str(tmp_path / "h.db")
    with history_client(db_path) as client:
        response = client.delete(DELETE_RANGE, params={"range": "all"})
        survivors = remaining_job_ids(db_path)

    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 3
    assert response.json()["skipped_active"] == 2
    assert survivors == {"recent-pending", "recent-processing", "other-recent"}


def test_another_users_history_is_never_touched(tmp_path) -> None:
    db_path = str(tmp_path / "i.db")
    with history_client(db_path) as client:
        client.delete(DELETE_RANGE, params={"range": "all"})
        survivors = remaining_job_ids(db_path)

    assert "other-recent" in survivors


def test_a_delete_with_an_empty_window_succeeds_and_reports_zero(tmp_path) -> None:
    """Nothing to delete is a success, not a 404 — the range is the request."""
    db_path = str(tmp_path / "j.db")
    with history_client(db_path) as client:
        client.delete(DELETE_RANGE, params={"range": "24h"})
        response = client.delete(DELETE_RANGE, params={"range": "24h"})

    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 0
    # The in-flight jobs are still counted as skipped, not as deleted.
    assert response.json()["skipped_active"] == 2


def test_the_delete_requires_authentication(tmp_path) -> None:
    db_path = str(tmp_path / "k.db")
    _seed(db_path)

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
    try:
        with TestClient(api_main.app) as client:
            response = client.delete(DELETE_RANGE, params={"range": "all"})
    finally:
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init

    assert response.status_code == 401
