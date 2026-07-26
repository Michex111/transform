from dataclasses import dataclass

from src.domain.subscriptions.value_object.tier import SubscriptionTier


@dataclass(frozen=True)
class ConversionActor:
    """Represents the actor attempting a conversion.

    Attributes:
        actor_key: Stable identifier used for quota/limit tracking.
        tier: Subscription tier that defines limits and queue priority.
        user_id: Persistent user identifier for authenticated users.
        ip_address: Client IP used for guest rate limiting.
    """

    actor_key: str
    tier: SubscriptionTier
    user_id: str | None = None
    ip_address: str | None = None


@dataclass(frozen=True)
class ConversionAuthorization:
    """Result of conversion authorization checks.

    Attributes:
        actor: Actor that was authorized.
        period_key: Monthly period used for credit accounting.
        credits_remaining: Remaining credits after deduction for persistent tiers.
        queue_stream: Queue stream selected for this actor tier.
    """

    actor: ConversionActor
    period_key: str
    credits_remaining: int | None
    queue_stream: str
