from dataclasses import dataclass

from src.domain.subscriptions.value_object.tier import SubscriptionTier


MB: int = 1024 * 1024  # 1 MB in bytes
GB: int = 1024 * MB  # 1 GB in bytes

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
    monthly_api_conversion_credits: int | None

    @property
    def api_access(self) -> bool:
        """Returns True if the tier allows API access."""
        return self.tier in {SubscriptionTier.FREE, SubscriptionTier.PREMIUM}

    @property
    def has_persistent_credits(self) -> bool:
        """Returns True if the tier has persistent monthly conversion credits."""
        return self.monthly_conversion_credits is not None

    @classmethod
    def for_tier(cls, tier: SubscriptionTier) -> 'TierPolicy':
        """Builds the default policy for a tier.

        Args:
            tier: Subscription tier to resolve.

        Returns:
            TierPolicy for the tier.
        """
        mapping: dict[SubscriptionTier, TierPolicy] = {
            SubscriptionTier.GUEST: TierPolicy(
                tier=SubscriptionTier.GUEST,
                storage_quota_bytes=50 * MB,
                monthly_conversion_credits=None,
                monthly_api_conversion_credits=None
            ),
            SubscriptionTier.FREE: TierPolicy(
                tier=SubscriptionTier.FREE,
                storage_quota_bytes=5 * GB,
                monthly_conversion_credits=50,
                monthly_api_conversion_credits=10
            ),
            SubscriptionTier.PREMIUM: TierPolicy(
                tier=SubscriptionTier.PREMIUM,
                storage_quota_bytes=100 * GB,
                monthly_conversion_credits=500,
                monthly_api_conversion_credits=100 # later derive valuses from config
            ),
        }
        
        return mapping[tier]
