from src.domain.conversions.entities.conversion_job import ConversionJob
from src.application.ports.contracts import FileStorageGateway as StoragePort
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from typing import Optional, Protocol


class CreditPort(Protocol):
    """Worker-usable access to monthly conversion credit buckets.

    The worker uses this port to resolve a user's tier, read the remaining
    credits for the current accounting period, and atomically consume credits
    after a successful conversion. This keeps the credit ledger behind the
    same dependency-inversion boundary as the rest of the worker's ports.
    """

    async def get_remaining(self, user_id: int, period_key: str) -> int | None:
        """Return remaining credits for ``period_key``, or ``None`` when no
        bucket exists yet (caller treats ``None`` as the full tier allowance)."""
        ...

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        """Resolve the user's current subscription tier."""
        ...

    async def consume(self, user_id: int, period_key: str, units: int) -> int:
        """Atomically deduct ``units`` from the bucket and return the NEW
        remaining (clamped at 0 — never negative).

        If no bucket exists, it is initialized from the user's tier allowance
        before deduction.
        """
        ...


class JobEventPort(Protocol):
    async def publish(
        self,
        job_id: str,
        status: str,
        progress: int,
        message: str | None = None,
        **kwargs: object,
    ) -> None:
        """Publish a job progress event.

        Extra keyword arguments (e.g. ``compute_duration_ms``,
        ``credits_used``, ``credits_remaining``) are forwarded as additional
        event fields by the concrete publisher.
        """
        ...

class QueuePort(Protocol):
    async def fetch_job(self) -> Optional[tuple[str, ConversionJob]]: ...

    async def acknowledge_job(self, message_id: str) -> None: ...

    async def fail_job(self, message_id: str, error_message: str) -> None: ...

    async def dead_letter_job(self, message_id: str, error_message: str, job: ConversionJob) -> None: ...

    async def reclaim_stale_jobs(self, min_idle_ms: int = 60_000, count: int = 20) -> int:
        """Best-effort reclaim of pending messages left by crashed workers."""
        return 0

class JobRepositoryPort(Protocol):
    """Persists job status transitions so the API can track progress."""

    async def update_conversion_job(self, job: ConversionJob) -> None: ...

    
