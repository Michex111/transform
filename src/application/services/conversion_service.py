from uuid import uuid4

from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.ports.database_port import ConversionJobRepositoryPort
from src.application.ports.queue_port import JobQueuePort
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.conversion_policy import is_supported
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

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return await self.db_repository.get_conversion_job(job_id)

