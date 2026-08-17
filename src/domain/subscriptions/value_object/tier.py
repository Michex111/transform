from enum import StrEnum

class SubscriptionTier(StrEnum):
    """Defines the supported subscription tiers.

    Values match the PostgreSQL ``subscriptiontier`` enum created in
    migration 0003_subscription_credit.
    """

    GUEST = "GUEST"
    FREE = "FREE"
    PREMIUM = "PREMIUM"
