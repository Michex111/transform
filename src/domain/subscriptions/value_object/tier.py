from enum import StrEnum

class SubscriptionTier(StrEnum):
    """Defines the supported subscription tiers."""

    GUEST = "guest"
    FREE = "free"
    PREMIUM = "premium"
