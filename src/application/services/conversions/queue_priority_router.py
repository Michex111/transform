from dataclasses import dataclass, field

from src.domain.subscriptions.value_object.tier import SubscriptionTier

from src.infrastructure.adapters.queues.stream_names import (
    JOB_STREAM,
    qualify,
)


def _tier_stream(suffix: str):
    """Deferred default so the environment namespace is applied per instance.

    A module-level constant would be resolved at import time, before settings
    are read, and would ignore QUEUE_STREAM_PREFIX.
    """
    return field(default_factory=lambda: qualify(f"{JOB_STREAM}:{suffix}"))


@dataclass(frozen=True)
class QueuePriorityRouter:
    """Resolves a Redis stream name from subscription tier."""

    guest_stream: str = _tier_stream("low")
    free_stream: str = _tier_stream("normal")
    premium_stream: str = _tier_stream("high")

    def stream_for_tier(self, tier: SubscriptionTier) -> str:
        """Returns the stream name for the provided tier.

        Args:
            tier: Subscription tier.

        Returns:
            Redis stream name that should receive this job.
        """
        mapping: dict[SubscriptionTier, str] = {
            SubscriptionTier.GUEST: self.guest_stream,
            SubscriptionTier.FREE: self.free_stream,
            SubscriptionTier.PREMIUM: self.premium_stream,
            SubscriptionTier.PRO: self.premium_stream,
            SubscriptionTier.PRO_PLUS: self.premium_stream,
            SubscriptionTier.ENTERPRISE: self.premium_stream,
        }
        return mapping[tier]