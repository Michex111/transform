class SubscriptionDomainError(Exception):
    """Base exception for subscription domain errors."""
    pass

class InvalidSubscriptionState(SubscriptionDomainError):
    """Raised when a subscription is in an invalid state for the requested operation."""
    pass

class StorageQuotaExceeded(SubscriptionDomainError):
    """Raised when subscription state is invalid."""
    pass

class GuestTierHasNoPersistentCredits(SubscriptionDomainError):
    """Raised when a guest tier subscription is attempted to be used with persistent credits."""
    pass

class InsufficientCredits(SubscriptionDomainError):
    """Raised when a subscription does not have enough credits for the requested operation."""
    pass

class InvalidCreditOperation(SubscriptionDomainError):
    """Raised when an invalid credit operation is attempted on a subscription."""
    pass

class GuestTierRequiresRateLimiting(SubscriptionDomainError):
    """Raised when a guest tier subscription is attempted to be used without rate limiting."""
    pass

class MissingCreditAccount(SubscriptionDomainError):
    """Raised when a subscription is missing a credit account for the requested operation."""
    pass
