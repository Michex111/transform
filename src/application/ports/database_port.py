from typing import Protocol, Optional
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.security.enitities.api_key import APIKey

class ConversionJobWriteRepositoryPort(Protocol):
    async def save_conversion_job(self, job_data: ConversionJob) -> None:
        """Save a conversion job to the database."""
        ...


class ConversionJobReadRepositoryPort(Protocol):
    """Reads conversion jobs for API use-cases."""

    async def get_conversion_job(self, job_id: str) -> Optional[ConversionJob]:
        """Retrieve a conversion job from the database by its ID."""
        ...

    async def list_user_history(
            self, 
            user_id: str, 
            offset: int, 
            limit: int
        ) -> tuple[list[ConversionJob], int]:
        """Returns user job history plus total count."""
        ...

    async def list_user_active_jobs(
            self, 
            user_id: str, 
            offset: int, 
            limit: int
        ) -> tuple[list[ConversionJob], int]:
        """Returns user jobs in pending/processing states plus total count."""
        ...

class SubscriptionRepositoryPort(Protocol):
    """Reads and updates actor subscription usage."""

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        """Returns the subscription tier for an actor key."""
        ...

    async def get_used_storage_bytes(self, actor_key: str) -> int:
        """Returns currently used storage for the actor key."""
        ...

    async def set_used_storage_bytes(self, actor_key: str, used_storage_bytes: int) -> None:
        """Stores current storage usage for the actor key."""
        ...


class CreditRepositoryPort(Protocol):
    """Persists monthly credit ledgers."""

    async def get_credit(self, owner_id: str, period_key: str) -> Optional[Credit]:
        """Returns credit state for a user and period."""
        ...

    async def save_credit(self, credit: Credit) -> None:
        """Stores credit state after updates."""
        ...

class ConversionJobRepositoryPort(ConversionJobWriteRepositoryPort, Protocol):
    """Repository interface for conversion jobs, combining read and write operations."""

    async def get_conversion_job(self, job_id: str) -> Optional[ConversionJob]:
        """Retrieve a conversion job from the database by its ID."""
        ...

class APIKeyRepositoryPort(Protocol):
    """Repository interface for API keys."""
    async def save_api_key(self, api_key: APIKey) -> None:
        """Save an API key to the database."""
        ...

    async def find_by_id(self, key: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its key."""
        ...

    async def find_by_key(self, key: str) -> Optional[APIKey]:
        """Retrieve an API key from the database by its key."""
        ...

    async def find_by_user(self, user_id: str) -> list[APIKey]:
        """Retrieve all API keys associated with a specific user."""
        ...

    async def update(self, api_key: APIKey) -> None:
        """Update an existing API key in the database."""
        ...

    async def delete(self, key: str) -> None:
        """Delete an API key from the database by its key."""
        ...