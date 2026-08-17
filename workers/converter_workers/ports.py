from src.domain.conversions.entities.conversion_job import ConversionJob
from src.application.ports.contracts import FileStorageGateway as StoragePort
from typing import Optional, Protocol


class JobEventPort(Protocol):
    async def publish(
        self,
        job_id: str,
        status: str,
        progress: int,
        message: str | None = None,
    ) -> None: ...

class QueuePort(Protocol):
    async def fetch_job(self) -> Optional[tuple[str, ConversionJob]]: ...

    async def acknowledge_job(self, message_id: str) -> None: ...

    async def fail_job(self, message_id: str, error_message: str) -> None: ...

    async def dead_letter_job(self, message_id: str, error_message: str, job: ConversionJob) -> None: ...

class JobRepositoryPort(Protocol):
    """Persists job status transitions so the API can track progress."""

    async def update_conversion_job(self, job: ConversionJob) -> None: ...

    
