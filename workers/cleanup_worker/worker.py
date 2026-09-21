"""
Cleanup worker — scheduled maintenance for guest data.

Runs as a separate process from the converter worker. Each cycle:

1. **Guest conversion jobs** — removes ownerless jobs (``user_id IS NULL``)
   older than the retention window along with their input/output objects,
   including jobs that were never completed (orphaned uploads).
2. **Expired guest files** — removes ``user_files`` rows whose ``expires_at``
   has passed (guest uploads) and their objects.
3. **Stale temp objects** — removes objects under the ``temp/`` prefix older
   than the retention window.
4. **Old job records** — removes conversion job history older than the archive
   window, releasing their storage objects.

All retention windows are configurable via environment settings.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select

from src.infrastructure.database.models import ConversionJobModel, UserFileModel

logger = logging.getLogger(__name__)


class ObjectStorage(Protocol):
    """Minimal object-storage surface needed by the cleanup worker."""

    def remove_object(self, key: str) -> bool: ...

    def list_objects(self, prefix: str) -> list[dict]: ...


class CleanupWorker:
    """
    Background worker that performs periodic cleanup of guest data.

    Responsibilities:
    - Remove expired guest conversion jobs and their objects
    - Remove guest files whose ``expires_at`` has passed
    - Remove stale temporary processing objects
    - Archive old conversion job records
    """

    def __init__(
        self,
        storage: ObjectStorage,
        db_session_factory,
        cleanup_interval: int = 6 * 60 * 60,  # every 6 hours
        guest_job_retention_hours: int = 24,
        guest_file_retention_hours: int = 24,
        temp_file_retention_hours: int = 1,
        job_archive_days: int = 30,
    ):
        self._storage = storage
        self._db_factory = db_session_factory
        self._interval = cleanup_interval
        self._guest_job_retention = timedelta(hours=guest_job_retention_hours)
        self._guest_file_retention = timedelta(hours=guest_file_retention_hours)
        self._temp_retention = timedelta(hours=temp_file_retention_hours)
        self._archive_after = timedelta(days=job_archive_days)
        self._running = False
        # Woken by ``stop()`` so a shutdown request does not have to wait out a
        # (potentially 6-hour) inter-cycle sleep.
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the periodic cleanup loop."""
        self._running = True
        logger.info("Cleanup worker started, interval=%d seconds", self._interval)

        while self._running:
            try:
                await self._run_cleanup_cycle()
            except asyncio.CancelledError:
                logger.info("Cleanup worker cancelled")
                self._running = False
                return
            except Exception as e:
                logger.error("Cleanup cycle failed: %s", e, exc_info=True)

            if not self._running:
                break
            try:
                # Wait for the interval, but return immediately when a
                # shutdown is requested instead of sleeping through it.
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    def stop(self) -> None:
        """Stop the cleanup loop after the current cycle."""
        self._running = False
        self._stop_event.set()
        logger.info("Cleanup worker stopped")

    async def _run_cleanup_cycle(self) -> None:
        """Execute a single cleanup cycle."""
        logger.info("Starting cleanup cycle")
        cycle_start = datetime.now(UTC)

        guest_jobs = await self._cleanup_guest_jobs()
        expired_files = await self._cleanup_expired_files()
        temp_objects = await self._cleanup_temp_files()
        archived_jobs = await self._archive_old_jobs()

        elapsed = (datetime.now(UTC) - cycle_start).total_seconds()
        # One summary line per cycle so a worker that runs but frees nothing is
        # distinguishable from one that is silently failing (W-10: the module
        # logger used to have no handler, so none of this was visible).
        logger.info(
            "Cleanup cycle completed in %.2f seconds "
            "(guest_jobs=%d, expired_files=%d, temp_objects=%d, archived_jobs=%d)",
            elapsed,
            guest_jobs,
            expired_files,
            temp_objects,
            archived_jobs,
        )

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def _cleanup_guest_jobs(self) -> int:
        """
        Remove guest conversion jobs older than the retention window.

        A guest job is a conversion whose ``user_id`` is NULL. Its input and
        output objects are deleted from storage, then the record is removed.

        Returns:
            Number of jobs cleaned up.
        """
        cutoff = datetime.now(UTC) - self._guest_job_retention
        cleaned = 0

        async with self._db_factory() as session:
            result = await session.execute(
                select(ConversionJobModel).where(
                    ConversionJobModel.user_id.is_(None),
                    ConversionJobModel.created_at < cutoff,
                )
            )
            rows = result.scalars().all()

            for row in rows:
                # ``input_file`` holds the user-facing display name, not the
                # object key (see the producer in conversion_service), and S3
                # DeleteObject succeeds for a nonexistent key — so deleting
                # the display name silently left every guest input in the
                # bucket forever, contradicting the documented 24h retention.
                await self._delete_objects(self._input_object_key(row), row.output_file)
                await session.delete(row)
                cleaned += 1

            if rows:
                await session.commit()

        if cleaned > 0:
            logger.info("Cleaned up %d expired guest conversion jobs", cleaned)
        return cleaned

    async def _cleanup_expired_files(self) -> int:
        """
        Remove user files whose expiry time has passed (guest uploads).

        Returns:
            Number of files cleaned up.
        """
        now = datetime.now(UTC)
        cutoff = now - self._guest_file_retention
        cleaned = 0

        async with self._db_factory() as session:
            result = await session.execute(
                select(UserFileModel).where(
                    UserFileModel.expires_at.is_not(None),
                    UserFileModel.expires_at < now,
                    UserFileModel.created_at < cutoff,
                )
            )
            rows = result.scalars().all()

            for row in rows:
                await self._delete_objects(row.file_key)
                await session.delete(row)
                cleaned += 1

            if rows:
                await session.commit()

        if cleaned > 0:
            logger.info("Cleaned up %d expired guest files", cleaned)
        return cleaned

    async def _cleanup_temp_files(self) -> int:
        """
        Remove temporary processing objects older than the retention window.

        Returns:
            Number of objects cleaned up.
        """
        cutoff = datetime.now(UTC) - self._temp_retention
        cleaned = 0

        try:
            objects = await asyncio.to_thread(self._storage.list_objects, "temp/")
        except Exception as e:
            logger.warning("Could not list temp objects: %s", e)
            return 0

        for obj in objects:
            last_modified = obj.get("last_modified")
            if not last_modified:
                continue
            try:
                modified = last_modified.replace(tzinfo=UTC) if last_modified.tzinfo is None else last_modified
            except AttributeError:
                continue
            if modified < cutoff:
                try:
                    await asyncio.to_thread(self._storage.remove_object, obj["object_name"])
                    cleaned += 1
                except Exception as e:
                    logger.warning("Could not delete temp object %s: %s", obj["object_name"], e)

        if cleaned > 0:
            logger.info("Cleaned up %d stale temp objects", cleaned)
        return cleaned

    async def _archive_old_jobs(self) -> int:
        """
        Archive conversion job records older than the archive window.

        Both objects and DB rows are removed. Completed jobs beyond the
        retention window are treated as expired history.

        Returns:
            Number of jobs archived.
        """
        cutoff = datetime.now(UTC) - self._archive_after
        archived = 0

        async with self._db_factory() as session:
            result = await session.execute(
                select(ConversionJobModel).where(ConversionJobModel.created_at < cutoff)
            )
            rows = result.scalars().all()

            for row in rows:
                await self._delete_objects(self._input_object_key(row), row.output_file)
                await session.delete(row)
                archived += 1

            if rows:
                await session.commit()

        if archived > 0:
            logger.info("Archived %d old conversion jobs", archived)
        return archived

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _input_object_key(row) -> str | None:
        """Object key of a job's input object.

        The producer stores the bare display filename in ``ConversionJobModel.
        input_file`` and the real object-store key in ``object_key``. The
        fallback keeps rows written before ``object_key`` existed cleanable.
        """
        return row.object_key or row.input_file

    async def _delete_objects(self, *keys: str | None) -> None:
        """Delete objects from storage, tolerating missing keys."""
        for key in keys:
            if not key:
                continue
            try:
                await asyncio.to_thread(self._storage.remove_object, key)
            except Exception as e:
                logger.warning("Could not delete object %s: %s", key, e)
