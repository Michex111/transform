from enum import StrEnum

class SubscriptionTier(StrEnum):
    """Defines the supported subscription tiers.

    Values match the PostgreSQL ``subscriptiontier`` enum created in
    migration 0003_subscription_credit (plus the added paid tiers in
    migration 0009_add_paid_tiers).
    """

    GUEST = "GUEST"
    FREE = "FREE"
    PREMIUM = "PREMIUM"  # Legacy alias for a paid tier; retained for compat
    PRO = "PRO"
    PRO_PLUS = "PRO_PLUS"
    ENTERPRISE = "ENTERPRISE"
