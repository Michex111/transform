"""Request/response schemas for the MCP (agent access) API surface.

These are the *user-facing* MCP endpoints — the consent screen's data source and
the Connected-apps management API. The OAuth protocol endpoints themselves
(``/mcp/authorize``, ``/mcp/token``, …) are served by the MCP SDK, so they have
no schemas here.
"""

from datetime import datetime

from pydantic import BaseModel, Field


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


class ConsentRequestResponse(BaseModel):
    """What the SPA renders before the user approves an application."""

    client_id: str
    client_name: str
    redirect_uri: str
    resource: str
    scopes: list[ScopeDescription]


class ConsentApprovalRequest(BaseModel):
    """The user's decision.

    ``scope`` is echoed from the authorization request so the server can
    re-derive (and re-validate) exactly the set the user was shown; only scopes
    inside that derived set can be approved, so a tampered page cannot widen a
    consent beyond what was displayed.
    """

    client_id: str = Field(min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=2000)
    code_challenge: str = Field(min_length=1, max_length=200)
    scope: str = Field(default="", max_length=500)
    resource: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    approved_scopes: list[str] = Field(default_factory=list)


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
