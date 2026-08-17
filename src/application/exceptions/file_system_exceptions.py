"""Application-layer exceptions for the user file system."""


class FileSystemError(Exception):
    """Base error for file-system operations. Carries the HTTP status code
    the presentation layer should map to."""

    status_code = 400


class FolderNotFoundError(FileSystemError):
    """The folder does not exist or does not belong to the caller."""

    status_code = 404

    def __init__(self, message: str = "Folder not found") -> None:
        super().__init__(message)


class FileRecordNotFoundError(FileSystemError):
    """The file record does not exist or does not belong to the caller."""

    status_code = 404

    def __init__(self, message: str = "File not found") -> None:
        super().__init__(message)


class FileSizeLimitExceededError(FileSystemError):
    """The uploaded file exceeds the caller's tier-based size limit."""

    status_code = 413

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FolderNameConflictError(FileSystemError):
    """A sibling folder with the same name already exists."""

    status_code = 409

    def __init__(self, message: str = "A folder with this name already exists") -> None:
        super().__init__(message)
