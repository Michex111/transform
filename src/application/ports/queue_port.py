from src.domain.entities.conversion_job import ConversionJob, JobStatus
from typing import Protocol, Optional


class JobQueuePort(Protocol):
    async def push_job(self, job: ConversionJob) -> None:...

class ProcessedJobQueuePort(Protocol):
    async def fetch_job(self, job_id: str) -> Optional[ConversionJob]:...

class PersistenceQueuePort(Protocol):
    async def enqueue_save(self, job: ConversionJob) -> None:...
