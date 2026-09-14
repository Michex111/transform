from datetime import datetime
from typing import Protocol, Optional
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.security.enitities.api_key import APIKey

class ConversionJobWriteRepositoryPort(Protocol):
    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """Save a conversion job to the database."""
        ...

    async def update_conversion_job(self, job: ConversionJob) -> None:
        """Persist progress updates for an existing job."""
        ...


class ConversionJobRepositoryPort(ConversionJobWriteRepositoryPort, Protocol):
    """Repository interface for conversion jobs, combining read and write operations."""

    async def get_conversion_job(self, job_id: str) -> Optional[ConversionJob]:
        """Retrieve a conversion job from the database by its ID."""
        ...

    async def list_user_history(
        self,
        user_id: int,
        offset: int,
        limit: int,
        since: datetime | None = None,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's job history (newest first) plus the total count.

        When ``since`` is provided, only jobs created on/after that timestamp
        are returned (used for the time-range filter on the History page).
        """
        ...

    async def delete_job(self, job_id: str, user_id: int) -> bool:
        """Delete a single job owned by ``user_id``. Returns True when a row was
        removed; False when the job is missing or not owned by the caller."""
        ...

    async def list_user_active_jobs(
        self,
        user_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionJob], int]:
        """Return the user's in-flight jobs (pending/processing/awaiting upload)."""
        ...

class APIKeyRepositoryPort(Protocol):
    """Repository interface for API keys."""
    async def save(self, api_key: APIKey) -> None:
        """Save an API key to the database."""
        ...

    async def get_by_id(self, api_key_id: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its ID."""
        ...

    async def find_by_key(self, key_hash: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its stored hash."""
        ...

    async def find_by_user(self, user_id: int) -> list[APIKey]:
        """Retrieve all API keys associated with a specific user."""
        ...

    async def update(self, api_key: APIKey) -> None:
        """Update an existing API key in the database."""
        ...

    async def touch_last_used(self, api_key_id: str) -> None:
        """Record usage time for an API key."""
        ...

    async def delete(self, api_key_id: str) -> bool:
        """Delete an API key from the database by its ID."""
        ...