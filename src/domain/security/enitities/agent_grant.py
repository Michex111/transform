"""A user's standing authorization for one AI agent (MCP client).

WHY this entity exists: an OAuth *refresh token* is a credential, but the thing
a user actually manages on the "Connected apps" screen is the **grant** — "the
Claude app may read and convert my documents, and last used them yesterday".
Modelling the grant (rather than deriving it from tokens) gives:

* one row to revoke, which takes effect immediately because access tokens are
  opaque and resolved through this row on every MCP request;
* a stable identity for the audit trail (``grant_id`` + ``client_id``);
* a natural home for the scope decision, so a re-consent cannot accidentally
  widen an existing grant.

The entity is pure: no storage, no clock, no I/O. Time is always passed in.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from src.domain.security.value_object.agent_scope import AgentScope, covers


class AgentGrantStatus(StrEnum):
    """Lifecycle of a grant. Mirrors the stored PostgreSQL enum values."""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass
class AgentGrant:
    """One user's consent for one OAuth client to act on their documents."""

    id: str
    user_id: int
    client_id: str
    #: Display name supplied by the client at registration. Shown on the
    #: consent screen and the Connected-apps list, so it is untrusted text and
    #: must be rendered as text (never HTML) by the SPA.
    client_name: str
    scopes: tuple[AgentScope, ...]
    status: AgentGrantStatus = AgentGrantStatus.ACTIVE
    #: RFC 8707 resource this grant is bound to (our MCP endpoint URL). A
    #: token minted from this grant may only be presented to that resource.
    resource: str | None = None
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    def is_active(self) -> bool:
        """Whether the grant may still be exercised."""
        return self.status is AgentGrantStatus.ACTIVE and len(self.scopes) > 0

    def covers(self, scope: AgentScope) -> bool:
        """Whether this (active) grant authorizes ``scope``.

        Revocation is enforced here as well as in the repository so a stale
        in-memory instance can never be the weak link.
        """
        return self.is_active() and covers(self.scopes, scope)

    def revoke(self, *, now: datetime) -> None:
        """Mark the grant revoked. Idempotent.

        The original ``revoked_at`` is preserved on a repeat call so the audit
        trail keeps the moment the user actually withdrew consent.
        """
        if self.status is not AgentGrantStatus.REVOKED:
            self.status = AgentGrantStatus.REVOKED
            self.revoked_at = now
