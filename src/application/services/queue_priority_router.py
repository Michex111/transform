from dataclasses import dataclass

from src.domain.subscriptions.value_object.tier import SubscriptionTier

@dataclass(frozen=True)
class QueuePriorityRouter:
    """Resolves a Redis stream name from subscription tier."""

    guest_stream: str = "conversion_jobs:low"
    free_stream: str = "conversion_jobs:normal"
    premium_stream: str = "conversion_jobs:high"

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
        }
        return mapping[tier]