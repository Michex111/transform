from dataclasses import dataclass

from src.domain.subscriptions.value_object.tier import SubscriptionTier


MB: int = 1024 * 1024
GB: int = 1024 * MB


@dataclass(frozen=True)
class TierPolicy:
    """Encapsulates quota and monthly credit policy for one subscription tier.

    Attributes:
        tier: Subscription tier this policy applies to.
        storage_quota_bytes: Maximum total storage allowed in bytes.
        monthly_conversion_credits: Monthly conversion credits. None means not persistent.
    """

    tier: SubscriptionTier
    storage_quota_bytes: int
    monthly_conversion_credits: int | None

    @property
    def has_persistent_credits(self) -> bool:
        """Returns whether this tier owns persistent monthly credits."""
        return self.monthly_conversion_credits is not None

    @classmethod
    def for_tier(cls, tier: SubscriptionTier) -> "TierPolicy":
        """Builds the default policy for a tier.

        Args:
            tier: Subscription tier to resolve.

        Returns:
            TierPolicy for the tier.
        """
        mapping: dict[SubscriptionTier, TierPolicy] = {
            SubscriptionTier.GUEST: cls(
                tier=SubscriptionTier.GUEST,
                storage_quota_bytes=50 * MB,
                monthly_conversion_credits=None,
            ),
            SubscriptionTier.FREE: cls(
                tier=SubscriptionTier.FREE,
                storage_quota_bytes=5 * GB,
                monthly_conversion_credits=100,
            ),
            SubscriptionTier.PREMIUM: cls(
                tier=SubscriptionTier.PREMIUM,
                storage_quota_bytes=50 * GB,
                monthly_conversion_credits=5000,
            ),
        }
        return mapping[tier]
