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

from src.domain.security.exceptions.exceptions import InvalidGrantTransition
from src.domain.security.value_object.agent_access_scope import (
    FolderAccess,
    HistoryScope,
    is_folder_restricted,
)
from src.domain.security.value_object.agent_scope import AgentScope, covers


class AgentGrantStatus(StrEnum):
    """Lifecycle of a grant.

    Stored in a plain ``VARCHAR`` column (see the MCP models), so adding a value
    here needs no migration — deliberately, because a new PostgreSQL enum value
    cannot be used in the same transaction that adds it.

    The three states answer different questions and are not interchangeable:

    * ``ACTIVE`` — the user has consented and the agent may work.
    * ``PAUSED`` — the user has *suspended* access. It is reversible by the user
      alone, the grant's scopes are preserved, and no new authorization flow is
      needed to undo it. This is the "stop this agent right now" control.
    * ``REVOKED`` — consent is withdrawn. The credentials are destroyed and
      regaining access requires a fresh OAuth consent, which is the point:
      revocation is the answer to "I no longer trust this application", and a
      merely-disabled flag would not be.
    """

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
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
    #: Which part of the Drive this consent reaches. ``ALL`` is the historical
    #: behaviour; ``FOLDER`` confines every operation to :attr:`folder_id` and
    #: everything beneath it.
    folder_access: FolderAccess = FolderAccess.ALL
    #: The folder a ``FOLDER`` grant is confined to. Deliberately paired with
    #: :attr:`folder_access` rather than used on its own: a *missing* folder on a
    #: restricted grant has to mean "deny", and a nullable column alone cannot
    #: distinguish that from "unrestricted".
    folder_id: str | None = None
    #: How much conversion history the agent may read. Defaults to the agent's
    #: own work, so an agent is never handed the user's unrelated history by
    #: omission.
    history_scope: HistoryScope = HistoryScope.AGENT
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    #: When the user last paused the grant, cleared on resume. Recorded so the
    #: UI can say when access was suspended, and so the audit question "how long
    #: was this agent blocked?" is answerable from the row itself.
    paused_at: datetime | None = None

    def is_active(self) -> bool:
        """Whether the grant may currently be exercised.

        A paused grant is **not** active. This single predicate is what enforces
        a pause: access tokens are opaque and resolved through this row on every
        MCP call, and that resolution refuses a grant for which this is false —
        so a pause takes effect on the very next tool call, with no reliance on
        a short token lifetime and no way for an existing session to slip past.
        """
        return self.status is AgentGrantStatus.ACTIVE and len(self.scopes) > 0

    def covers(self, scope: AgentScope) -> bool:
        """Whether this (active) grant authorizes ``scope``.

        Revocation and pausing are enforced here as well as in the repository so
        a stale in-memory instance can never be the weak link.
        """
        return self.is_active() and covers(self.scopes, scope)

    def is_folder_restricted(self) -> bool:
        """Whether every operation must be proven to sit inside a folder."""
        return is_folder_restricted(self.folder_access, self.folder_id)

    def pause(self, *, now: datetime) -> None:
        """Suspend the grant without destroying it. Idempotent.

        Refuses to move a revoked grant: revocation is terminal by design, and
        a pause that silently "resurrected" a revoked grant into a resumable
        state would make ``resume`` a way back in without a new consent.
        """
        if self.status is AgentGrantStatus.REVOKED:
            raise InvalidGrantTransition("A revoked grant cannot be paused.")
        if self.status is not AgentGrantStatus.PAUSED:
            self.status = AgentGrantStatus.PAUSED
            self.paused_at = now

    def resume(self) -> None:
        """Undo a pause, restoring exactly the scopes already granted.

        Never widens access: the scope tuple is untouched, so a resume after a
        re-consent that narrowed the scopes cannot bring the old ones back.
        """
        if self.status is AgentGrantStatus.REVOKED:
            raise InvalidGrantTransition("A revoked grant cannot be resumed.")
        if self.status is AgentGrantStatus.PAUSED:
            self.status = AgentGrantStatus.ACTIVE
            self.paused_at = None

    def revoke(self, *, now: datetime) -> None:
        """Mark the grant revoked. Idempotent.

        The original ``revoked_at`` is preserved on a repeat call so the audit
        trail keeps the moment the user actually withdrew consent.
        """
        if self.status is not AgentGrantStatus.REVOKED:
            self.status = AgentGrantStatus.REVOKED
            self.revoked_at = now
