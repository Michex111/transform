"""Dependency wiring for the cleanup worker."""

from src.infrastructure.adapters.storage.minio_storage_factory import get_storage
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from workers.cleanup_worker.worker import CleanupWorker


def build_cleanup_worker() -> CleanupWorker:
    """Construct a configured CleanupWorker from environment settings."""
    settings = get_settings()
    return CleanupWorker(
        storage=get_storage(),
        db_session_factory=get_session_factory(),
        cleanup_interval=settings.CLEANUP_INTERVAL_SECONDS,
        guest_job_retention_hours=settings.GUEST_JOB_RETENTION_HOURS,
        guest_file_retention_hours=settings.GUEST_FILE_RETENTION_HOURS,
        temp_file_retention_hours=settings.TEMP_FILE_RETENTION_HOURS,
        job_archive_days=settings.JOB_ARCHIVE_AFTER_DAYS,
    )
