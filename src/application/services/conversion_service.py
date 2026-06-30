from src.domain.entities.conversion_job import ConversionJob
from src.domain.services.conversion_policy import is_supported
from src.infrastructure.converters.converter_registry import get_registry
from src.application.ports.contracts import JobQueuePort, JobStoragePort

class ConversionService:
    def __init__(self, queue_port: JobQueuePort, storage_port: JobStoragePort | None = None):
        self.queue_port = queue_port
        self.storage_port = storage_port

    async def submit_conversion_job(self, job: ConversionJob) -> str:
        conversion_type = job.conversion
        is_supported(conversion_type, get_registry().list_conversions())
        await self.queue_port.push_job(job)
        # self.storage_port.save_job(job)
        return job.job_id
