"""A short-lived intent to receive one file from an MCP client's sandbox.

WHY this exists: an MCP client's chat attachment never reaches this server. The
model sees extracted text and images, not the original bytes, so it cannot
rebuild a PDF or a DOCX for ``upload_file(content_base64)``. The intent is the
missing byte path: the agent mints a capability, the client's sandbox (or the
user's browser) uploads the real file to it, and the server stores the file
through the ordinary upload pipeline.

The intent row is deliberately *not* the authority. It records what was
authorized (which grant, which folder, which size ceiling) so that a redemption
can re-check every one of those facts against the grant that is stored *now*.
A capability is a handle; the grant remains the permission — exactly as it is
for an access token.

The entity is pure: no storage, no clock, no I/O. Time is always passed in.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MCPUploadIntentStatus(StrEnum):
    """Lifecycle of one upload intent.

    Stored in a plain ``VARCHAR`` column (like the MCP OAuth tables), so adding
    a value needs no migration. The values answer different questions:

    * ``PENDING`` — minted, not redeemed yet. The only state a capability can
      move out of.
    * ``RECEIVING`` — the capability has been redeemed (exactly once) and bytes
      may be arriving. A second redemption is a replay and is refused.
    * ``COMPLETED`` — the bytes became a Drive file. ``file_id`` is set.
    * ``FAILED`` — the upload was refused or broke. ``error`` carries a
      user-safe sentence.
    * ``EXPIRED`` — the link's window elapsed. Set by housekeeping, not by a
      redemption (an expired row simply fails the time predicate).
    """

    PENDING = "PENDING"
    RECEIVING = "RECEIVING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"

    @classmethod
    def coerce(cls, value: str | None) -> "MCPUploadIntentStatus":
        """Parse a stored value, failing closed to ``EXPIRED``.

        An unrecognised status must never read as ``PENDING``: that would make a
        row whose status a migration mangled redeemable again.
        """
        try:
            return cls(value or "")
        except ValueError:
            return cls.EXPIRED


@dataclass
class MCPUploadIntent:
    """One authorized, single-use upload of one file into the user's Drive."""

    id: str
    user_id: int
    #: The ``mcp_agent_grants`` row this upload was authorized under. Every
    #: redemption re-reads that row: a revoked, paused or narrowed grant, or one
    #: moved to another folder, refuses the upload.
    grant_id: str
    client_id: str
    #: Sanitized leaf name, as it will appear in the Drive.
    file_name: str
    extension: str
    #: ``min(tier per-file cap, MCP_ATTACHMENT_MAX_BYTES)`` at creation time.
    max_bytes: int
    #: Destination folder, already resolved against the grant's subtree.
    folder_id: str | None = None
    #: When set, a conversion to this format starts after the upload lands.
    target_format: str | None = None
    status: MCPUploadIntentStatus = MCPUploadIntentStatus.PENDING
    #: The created Drive file, once ``COMPLETED``.
    file_id: str | None = None
    #: The conversion job started by ``complete``, when ``target_format`` is set.
    job_id: str | None = None
    #: User-safe failure sentence; never a storage key or an internal message.
    error: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    #: Set atomically when the capability is redeemed. Its presence is what
    #: makes a second redemption a replay rather than a retry.
    used_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at <= now

    def is_pending(self, now: datetime) -> bool:
        """Whether a capability for this intent may still start an upload."""
        return (
            self.status is MCPUploadIntentStatus.PENDING
            and self.used_at is None
            and not self.is_expired(now)
        )
