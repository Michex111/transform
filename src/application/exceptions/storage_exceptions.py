class StorageOperationError(Exception):
     """Custom exception for storage operation errors."""
     pass

class StorageUnavailableError(StorageOperationError):
    """Raised when the storage service is unavailable."""
    pass

class ObjectNotFoundError(StorageOperationError):
    """Raised when the requested object is not found in storage."""
    pass

class StoragePermissionError(StorageOperationError):
    """Raised when there is a permission issue accessing the storage."""
    pass