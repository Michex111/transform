"""Request/response schemas for the MCP (agent access) API surface.

These are the *user-facing* MCP endpoints — the consent screen's data source and
the Connected-apps management API. The OAuth protocol endpoints themselves
(``/mcp/authorize``, ``/mcp/token``, …) are served by the MCP SDK, so they have
no schemas here.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from src.domain.security.value_object.agent_access_scope import (
    FolderAccess,
    HistoryScope,
)


class ScopeDescription(BaseModel):
    """One selectable permission on the consent screen."""

    scope: str
    description: str
    #: Whether this scope was part of the client's request (only requested
    #: scopes may be approved).
    requested: bool
    #: Whether the user had already granted it, so the screen can say
    #: "already allowed" instead of pretending the decision is new.
    already_granted: bool = False
    #: Whether granting it allows an irreversible action. Sent as data rather
    #: than left for the SPA to infer from the scope *name*, so the "never
    #: pre-select this" rule lives next to the list of destructive scopes in the
    #: domain instead of being duplicated as a string literal in the UI.
    destructive: bool = False


class ConsentFolderOption(BaseModel):
    """One folder the user may confine an agent to, for the consent picker."""

    folder_id: str
    name: str


class ConsentRequestResponse(BaseModel):
    """What the SPA renders before the user approves an application."""

    client_id: str
    client_name: str
    redirect_uri: str
    resource: str
    scopes: list[ScopeDescription]
    #: The user's folders, for the picker. Bounded by the router (see
    #: ``_MAX_CONSENT_FOLDERS``) so a large Drive cannot turn the consent screen
    #: into an unbounded response.
    folders: list[ConsentFolderOption] = Field(default_factory=list)
    #: The binding the user currently has (or the default when there is none),
    #: so re-consenting shows the existing choice instead of resetting it
    #: silently. The wire values are the domain enums so they cannot drift.
    folder_access: FolderAccess = FolderAccess.ALL
    folder_id: str | None = None
    history_scope: HistoryScope = HistoryScope.AGENT
    #: Whether this server lets the user pick a history scope at all. Sent as
    #: data so the SPA does not have to hard-code the policy.
    can_choose_history_scope: bool = True


class ConsentApprovalRequest(BaseModel):
    """The user's decision.

    ``scope`` is echoed from the authorization request so the server can
    re-derive (and re-validate) exactly the set the user was shown; only scopes
    inside that derived set can be approved, so a tampered page cannot widen a
    consent beyond what was displayed.

    ``folder_access``, ``folder_id`` and ``history_scope`` carry the binding the
    user chose. The router resolves ``new_folder_name``/``folder_id`` against the
    user's own folders before anything is persisted, so these values name a
    folder the user actually owns (or are discarded for whole-Drive consent).
    """

    client_id: str = Field(min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=2000)
    code_challenge: str = Field(min_length=1, max_length=200)
    scope: str = Field(default="", max_length=500)
    resource: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    approved_scopes: list[str] = Field(default_factory=list)
    folder_access: FolderAccess = FolderAccess.ALL
    folder_id: str | None = Field(default=None, max_length=64)
    new_folder_name: str | None = Field(default=None, max_length=200)
    history_scope: HistoryScope = HistoryScope.AGENT


class ConsentApprovalResponse(BaseModel):
    """Where the browser must go next — the client's redirect with the code."""

    redirect_url: str


class ConnectedAppResponse(BaseModel):
    """One application the user has granted access to."""

    id: str
    client_id: str
    client_name: str
    scopes: list[str]
    status: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ConnectedAppListResponse(BaseModel):
    apps: list[ConnectedAppResponse]


class FolderAccessEntry(BaseModel):
    """One folder an active, folder-confined grant may work in."""

    folder_id: str
    client_name: str
    grant_id: str


class FolderAccessResponse(BaseModel):
    """The folders the user's active agents are currently confined to.

    The Files page uses this to mark which folders an authorized agent can see;
    it exposes only the folder identity and the agent's display name, never a
    credential or the grant's other internals.
    """

    folders: list[FolderAccessEntry] = Field(default_factory=list)

