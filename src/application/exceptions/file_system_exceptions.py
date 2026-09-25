"""Application-layer exceptions for the user file system."""


class FileSystemError(Exception):
    """Base error for file-system operations. Carries the HTTP status code
    the presentation layer should map to."""

    status_code = 400

    def http_detail(self) -> object:
        """The ``detail`` payload the router should put in the HTTPException.

        Defaults to the plain message string, which is the historical shape of
        every file-system error response. Structured errors (the upload-limit
        rejections the SPA branches on) override this to return a dict carrying
        a machine-readable ``code``. Routers must always use this method rather
        than ``str(exc)`` so the shape lives with the error, not the route.
        """
        return str(self)


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
    """The uploaded file exceeds the caller's tier-based per-file size cap.

    ``status_code = 413`` with a structured ``detail`` so the SPA can branch on
    ``code`` ("FILE_TOO_LARGE") instead of matching on human-readable prose.
    The optional byte counts are omitted by legacy callers that have no numbers
    handy; in that case the response degrades to the plain message string.
    """

    status_code = 413
    code = "FILE_TOO_LARGE"

    def __init__(
        self,
        message: str,
        *,
        max_file_size_bytes: int | None = None,
        file_size: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.max_file_size_bytes = max_file_size_bytes
        self.file_size = file_size

    def http_detail(self) -> object:
        if self.max_file_size_bytes is None:
            return self.message
        return {
            "code": self.code,
            "message": self.message,
            "max_file_size_bytes": self.max_file_size_bytes,
            "file_size": self.file_size,
        }


class StorageQuotaExceededError(FileSystemError):
    """The upload would push the account's total storage over its quota.

    Distinct from :class:`FileSizeLimitExceededError` on purpose: the per-file
    cap and the account quota are independent controls, and the SPA shows a
    different remedy for each ("this file is too big" vs "free up space").
    Carries the exact numbers the UI needs to explain the shortfall.
    """

    status_code = 413
    code = "STORAGE_QUOTA_EXCEEDED"

    def __init__(
        self,
        message: str,
        *,
        limit_bytes: int,
        used_bytes: int,
        available_bytes: int,
        file_size: int,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.limit_bytes = limit_bytes
        self.used_bytes = used_bytes
        self.available_bytes = available_bytes
        self.file_size = file_size

    def http_detail(self) -> object:
        return {
            "code": self.code,
            "message": self.message,
            "limit_bytes": self.limit_bytes,
            "used_bytes": self.used_bytes,
            "available_bytes": self.available_bytes,
            "file_size": self.file_size,
        }


class FileTypeMismatchError(FileSystemError):
    """The uploaded content's magic bytes do not match the claimed extension."""

    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FolderNameConflictError(FileSystemError):
    """A sibling folder with the same name already exists."""

    status_code = 409

    def __init__(self, message: str = "A folder with this name already exists") -> None:
        super().__init__(message)
