class DomainError(Exception):
    """Base class for all domain exceptions."""
    pass

class InvalidConversion(DomainError):
    """Raised when an invalid conversion is attempted."""
    pass

class InvalidStateTransition(DomainError):
    """Raised when an invalid state transition is attempted."""
    pass