class StorageOperationError(Exception):
    """Base exception for storage operation errors."""


class StorageUnavailableError(StorageOperationError):
    """Raised when the storage service is unavailable."""


class ObjectNotFoundError(StorageOperationError):
    """Raised when the requested object is not found in storage."""


class StoragePermissionError(StorageOperationError):
    """Raised when there is a permission issue accessing the storage."""
