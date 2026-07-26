class SubscriptionApplicationError(Exception):
    """Base exception for subscription orchestration errors."""


class GuestRateLimitExceeded(SubscriptionApplicationError):
    """Raised when a guest request exceeds configured rate limits."""


class MissingActorIdentity(SubscriptionApplicationError):
    """Raised when an actor payload lacks fields required for a workflow."""
