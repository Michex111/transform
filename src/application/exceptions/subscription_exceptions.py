class SubscriptionApplicationError(Exception):
    """Base exception for subscription orchestration errors."""

class GuestRateLimitExceeded(SubscriptionApplicationError):
    """Raised when a guest tier subscription exceeds its rate limit."""

class MissingActorIdentity(SubscriptionApplicationError):
    """Raised when a conversion request is missing actor identity information."""