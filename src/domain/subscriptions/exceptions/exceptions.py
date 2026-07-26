class SubscriptionDomainError(Exception):
    """Base exception for subscription domain errors."""


class InvalidSubscriptionState(SubscriptionDomainError):
    """Raised when subscription state is invalid."""


class StorageQuotaExceeded(SubscriptionDomainError):
    """Raised when a storage operation exceeds the tier quota."""


class InvalidCreditOperation(SubscriptionDomainError):
    """Raised when a credit operation is invalid."""


class MissingCreditAccount(SubscriptionDomainError):
    """Raised when a conversion requires credits but no credit account is provided."""


class InsufficientCredits(SubscriptionDomainError):
    """Raised when there are not enough credits for conversion."""


class GuestTierHasNoPersistentCredits(SubscriptionDomainError):
    """Raised when code tries to create persistent credits for a guest tier."""


class GuestTierRequiresRateLimiting(SubscriptionDomainError):
    """Raised when a guest conversion bypasses the guest rate-limit path."""
