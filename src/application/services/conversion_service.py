from uuid import uuid4

from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.ports.database_port import ConversionJobRepositoryPort
from src.application.ports.queue_port import JobQueuePort
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.conversion_policy import is_supported
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.converters.converter_registry import get_registry


class ConversionService:
    def __init__(
        self,
        queue_port: JobQueuePort,
        db_repository: ConversionJobRepositoryPort,
        queue_dispatcher: PriorityQueueDispatcher | None = None,
    ):
        self.queue_port = queue_port
        self.db_repository = db_repository
        self.queue_dispatcher = queue_dispatcher

    async def create_conversion_job(self, job: ConversionJob) -> str:
        """Validate the conversion type, assign an ID, and persist the job."""
        is_supported(job.conversion, get_registry().list_conversions())
        job.job_id = str(uuid4())
        await self.db_repository.save_conversion_job(job)
        return job.job_id

    async def push_conversion_job(
        self,
        job: ConversionJob,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> str:
        """Enqueue an already-persisted job for processing.

        The job must already have a ``job_id`` assigned. Its status is moved
        from ``AWAITING_UPLOAD`` to ``PENDING`` before it is published so that
        workers can immediately start processing it.
        """
        if not job.job_id:
            raise InvalidConversionJobError(
                "Job ID must be set before pushing the job to the queue."
            )

        is_supported(job.conversion, get_registry().list_conversions())

        # Persist the PENDING state transition before publishing
        if job.status.value == "AWAITING_UPLOAD":
            job.pending_processing()
            await self.db_repository.update_conversion_job(job)

        if self.queue_dispatcher is not None:
            await self.queue_dispatcher.dispatch(job, tier=tier)
        else:
            await self.queue_port.publish_job(job)
        return job.job_id

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> ConversionJob:
        """Create and enqueue a job that converts a file already stored in the
        user's library.

        Unlike the upload flow, the input object is already persisted at
        ``object_key``, so the job is created directly and enqueued immediately
        (no separate upload/verify step). The worker downloads the existing
        object and converts it.

        Raises:
            InvalidConversion: If ``source_format`` / ``target_format`` is not
                a supported conversion.
        """
        job = ConversionJob(
            job_id="",
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=file_name,
            object_key=object_key,
            user_id=user_id,
        )
        await self.create_conversion_job(job)
        await self.push_conversion_job(job, tier=tier)
        return job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return await self.db_repository.get_conversion_job(job_id)

    async def list_history(
        self,
        user_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        since=None,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's conversion history (newest first) plus total count.

        Args:
            since: Optional datetime; only jobs created on/after this time are
                returned (used for the time-range filter on the History page).
        """
        return await self.db_repository.list_user_history(user_id, offset, limit, since=since)

    async def delete_history_job(self, job_id: str, user_id: int) -> bool:
        """Delete a single conversion-history record owned by ``user_id``.

        Returns:
            True if a job was deleted; False if it was not found or not owned.
        """
        return await self.db_repository.delete_job(job_id, user_id)

    async def update_conversion_job(self, job: ConversionJob) -> None:
        """Persist progress updates for an existing job (status, output, errors,
        compute time and credits consumed).
        """
        await self.db_repository.update_conversion_job(job)

    async def retry_conversion_job(
        self,
        job_id: str,
        user_id: int | None,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> ConversionJob:
        """Re-enqueue a failed job without re-uploading its input file.

        The input object is already stored at ``object_key``, so retrying only
        resets the job to PENDING and pushes it back to the queue. The worker
        downloads the existing object and tries again.

        Raises:
            InvalidConversionJobError: If the job does not exist, is not owned
                by the caller, or is not in a retryable (FAILED) state.
        """
        job = await self.db_repository.get_conversion_job(job_id)
        if not is_job_owner(job, user_id):
            raise InvalidConversionJobError("Job not found")

        # Reset FAILED -> PENDING (keeps object_key so the existing file is reused).
        job.retry()
        await self.db_repository.update_conversion_job(job)

        # Re-enqueue with the same object_key.
        if self.queue_dispatcher is not None:
            await self.queue_dispatcher.dispatch(job, tier=tier)
        else:
            await self.queue_port.publish_job(job)
        return job

