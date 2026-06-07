from domain.entities.conversion_job import ConversionJob
from domain.services.conversion_policy import is_supported
from infrastructure.converters.converter_registry import converter_registry
from application.ports.queue_port import JobQueuePort
from application.ports.storage_port import StoragePort

class ConversionService:
    def __init__(self, queue_port: JobQueuePort, storage_port: StoragePort):
        self.queue_port = queue_port
        self.storage_port = storage_port

    async def submit_conversion_job(self, job: ConversionJob) -> str:
        conversion_type = job.conversion
        is_supported(conversion_type, converter_registry.list_conversions())
        self.queue_port.enqueue_job(job)
        # self.storage_port.save_job(job)
        return job.job_id
