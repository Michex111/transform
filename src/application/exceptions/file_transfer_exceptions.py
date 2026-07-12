class FileTransferError(Exception):
    """Base exception for file transfer service errors."""


class UploadSessionNotFoundError(FileTransferError):
    """Raised when an upload session is not found in the cache."""


class UploadVerificationError(FileTransferError):
    """Raised when an upload session exists but the upload is not complete."""
