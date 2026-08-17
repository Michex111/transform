from typing import Protocol
from src.domain.conversions.entities.conversion_job import ConversionJob


class JobQueuePort(Protocol):
    async def publish_job(self, job: ConversionJob, stream: str | None = None) -> None: ...

