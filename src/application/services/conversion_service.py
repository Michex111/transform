from application.ports.db_repository import ConversionJobRepository
from domain.entities.conversion_job import ConversionJob
from domain.services.conversion_policy import is_supported
from infrastructure.converters.converter_registry import get_registry
from application.ports.contracts import JobQueuePort, JobStoragePort
from application.exceptions.conversion_job_exception import InvalidConversionJobError

from uuid import uuid4

class ConversionService:
    def __init__(self, queue_port: JobQueuePort, db_repository: ConversionJobRepository):
        self.queue_port = queue_port
        self.db_repository = db_repository

    async def create_conversion_job(self, job: ConversionJob) -> str:
        is_supported(job.conversion, get_registry().list_conversions())
        job.job_id = str(uuid4())
        await self.db_repository.save_conversion_job(job)
        return job.job_id

    async def push_conversion_job(self, job: ConversionJob) -> str:
        conversion_type = job.conversion
        is_supported(conversion_type, get_registry().list_conversions())
        await self.queue_port.push_job(job)
        # self.storage_port.save_job(job)
        if not job.job_id:
            raise InvalidConversionJobError("Job ID must be set before pushing the job to the queue.")
        return job.job_id
    
   



        # To be continued ...
