class ApiKeyError(Exception):
    """Base class for API key related errors."""
    pass

class UnauthorizedError(ApiKeyError):
    """Raised when an API key is invalid or unauthorized."""
    pass

