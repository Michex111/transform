class FileTransferError(Exception):
    """Base exception for file transfer service errors."""


class UploadSessionNotFoundError(FileTransferError):
    """Raised when an upload session is not found in the cache."""


class UploadVerificationError(FileTransferError):
    """Raised when an upload session exists but the upload is not complete."""


class UploadSessionExpiredError(FileTransferError):
    """The upload session is gone from the cache.

    A session that was never valid and one that has expired are
    indistinguishable through the cache (both are a miss), so they share this
    error. The upload-parts endpoint maps it to ``410 Gone`` rather than
    ``404``: the only recovery either way is to start a new session, and
    ``410`` tells the SPA exactly that ("gone, start over") instead of leaving
    it to guess whether the id was ever valid.
    """


class UploadNotMultipartError(FileTransferError):
    """Part URLs were requested for a single-PUT session."""


class MissingUploadPartsError(FileTransferError):
    """A multipart session was finalized without its part list."""


class InvalidPartNumberError(FileTransferError):
    """A requested part number is outside ``1..part_count``."""

