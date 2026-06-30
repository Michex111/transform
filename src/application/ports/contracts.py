from pathlib import Path
from typing import Optional, Protocol

from src.domain.entities.conversion_job import ConversionJob


class JobQueuePort(Protocol):
    async def push_job(self, job: ConversionJob) -> None: ...


class ProcessedJobQueuePort(Protocol):
    async def fetch_job(self, job_id: str) -> Optional[ConversionJob]: ...


class PersistenceQueuePort(Protocol):
    async def enqueue_save(self, job: ConversionJob) -> None: ...


class JobStoragePort(Protocol):
    def save_job(self, job: ConversionJob) -> None: ...

    def get_job_by_id(self, job_id: str) -> ConversionJob: ...


class StorageGateway(Protocol):
    def download(self, key: str, dest_path: Path) -> None: ...

    def upload(self, target_key: str, source_path: Path) -> None: ...


class StoragePort(Protocol):
    def download(self, key: str, dest_path: Path) -> None: ...

    def upload(self, target_key: str, source_path: Path) -> None: ...


class QueuePort(Protocol):
    async def fetch_job(self) -> Optional[tuple[str, ConversionJob]]: ...

    async def acknowledge_job(self, message_id: str) -> None: ...

    async def fail_job(self, message_id: str, error_message: str) -> None: ...


class JobEventPort(Protocol):
    async def publish(
        self,
        job_id: str,
        status: str,
        progress: int,
        message: str | None = None,
    ) -> None: ...
