from typing import Protocol
from src.domain.conversions.entities.conversion_job import ConversionJob

class ConversionJobRepository(Protocol):
    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """Save a conversion job to the database."""
        ...

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """Retrieve a conversion job from the database by its ID."""
        ...