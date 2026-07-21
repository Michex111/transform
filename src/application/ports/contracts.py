from pathlib import Path
from typing import Optional, Protocol

from src.domain.conversions.entities.conversion_job import ConversionJob


class JobQueuePort(Protocol):
    async def push_job(self, job: ConversionJob) -> None: ...


class ProcessedJobQueuePort(Protocol):
    async def fetch_job(self, job_id: str) -> Optional[ConversionJob]: ...


class PersistenceQueuePort(Protocol):
    async def enqueue_save(self, job: ConversionJob) -> None: ...


class JobStoragePort(Protocol):
    def save_job(self, job: ConversionJob) -> None: ...

    def get_job_by_id(self, job_id: str) -> ConversionJob: ...

class FileStorageGateway(Protocol):
    def download(self, key: str, dest_path: Path) -> None: ...

    def upload(self, target_key: str, source_path: Path) -> None: ...

# TODO: clean up required for unrequired ports 






