from domain.entities.conversion_job import ConversionJob, JobStatus
from typing import Protocol, Optional


class JobQueuePort(Protocol):
    def enqueue_job(self, job: ConversionJob) -> None:...
    def fetch_job(self) -> Optional[ConversionJob]:...

class ProcessedJobQueuePort(Protocol):
    def fetch_job(self, job_id: str) -> Optional[ConversionJob]:...

class PersistenceQueuePort(Protocol):
    def enqueue_save(self, job: ConversionJob) -> None:...

    
