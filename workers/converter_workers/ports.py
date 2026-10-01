from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.application.ports.contracts import FileStorageGateway as StoragePort
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from typing import Optional, Protocol


class CreditPort(Protocol):
    """Worker-usable access to a user's credit wallet.

    The worker uses this port to resolve a user's tier, read the spendable
    balance for the current accounting period, and atomically consume credits
    after a successful conversion. This keeps the credit ledger behind the
    same dependency-inversion boundary as the rest of the worker's ports.

    The wallet has three populations with different lifetimes (plan, carryover,
    purchased); ``get_remaining`` still reports the *plan* bucket for
    compatibility, while ``get_available_total`` reports the whole wallet:
    a pre-check that only looked at the plan bucket would refuse an API user
    who has run out of plan credits but still holds purchased ones.
    """

    async def get_remaining(self, user_id: int, period_key: str) -> int | None:
        """Return remaining *plan* credits for ``period_key``, or ``None`` when
        no bucket exists yet (caller treats ``None`` as the full tier
        allowance)."""
        ...

    async def get_available_total(self, user_id: int, period_key: str) -> int | None:
        """Return spendable credits across all three populations.

        ``None`` means "no finite allowance to gate on" — either the tier has
        unlimited credits (Enterprise) or no bucket exists and the tier itself
        has no persistent credits. Callers must treat ``None`` as "do not
        refuse", matching the pre-existing best-effort behaviour.
        """
        ...

    async def get_tier(self, user_id: int) -> SubscriptionTier:
        """Resolve the user's current subscription tier."""
        ...

    async def consume(
        self,
        user_id: int,
        period_key: str,
        units: int,
        *,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> int:
        """Atomically deduct ``units`` from the wallet and return the NEW plan
        remaining (clamped at 0 — never negative).

        ``origin`` decides only the *order* the non-expiring pools are spent in
        (an API caller may prefer purchased credits first); the total available
        is origin-independent, and carryover is always spent first.

        If no plan bucket exists, it is initialized from the user's tier
        allowance before deduction.
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

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        """Read the persisted row for a job.

        Used by the processor's idempotency guard before it converts anything,
        so a redelivered job cannot be converted — and charged — twice.
        """
        ...

    
