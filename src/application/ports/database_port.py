from typing import Protocol

from src.application.dtos.job_views import ActiveQueueItem, ConversionHistoryItem
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.subscriptions.entities.credit import Credit
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class ConversionJobWriteRepository(Protocol):
    """Persists conversion jobs."""

    async def save_conversion_job(self, job: ConversionJob) -> None:
        """Stores a conversion job."""
        ...


class ConversionJobReadRepository(Protocol):
    """Reads conversion jobs for API use-cases."""

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """Returns a single conversion job by identifier."""
        ...

    async def list_user_history(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ConversionHistoryItem], int]:
        """Returns user job history plus total count."""
        ...

    async def list_user_active_jobs(
        self,
        user_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ActiveQueueItem], int]:
        """Returns user jobs in pending/processing states plus total count."""
        ...


class SubscriptionRepository(Protocol):
    """Reads and updates actor subscription usage."""

    async def get_actor_tier(self, actor_key: str) -> SubscriptionTier:
        """Returns subscription tier for a tracked actor key."""
        ...

    async def get_used_storage_bytes(self, actor_key: str) -> int:
        """Returns currently used storage for the actor key."""
        ...

    async def set_used_storage_bytes(self, actor_key: str, used_storage_bytes: int) -> None:
        """Stores current storage usage for the actor key."""
        ...


class CreditRepository(Protocol):
    """Persists monthly credit ledgers."""

    async def get_credit(self, owner_id: str, period_key: str) -> Credit | None:
        """Returns credit state for a user and period."""
        ...

    async def save_credit(self, credit: Credit) -> None:
        """Stores credit state after updates."""
        ...
