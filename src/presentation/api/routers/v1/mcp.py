"""User-facing MCP endpoints: OAuth discovery, consent, and connected apps.

The OAuth protocol itself (``/mcp/authorize``, ``/mcp/token``, ``/mcp/register``,
``/mcp/revoke``) is served by the MCP SDK's sub-application. This module adds the
three things the SDK cannot provide, because they are Transform's own:

1. **Discovery documents at their canonical URLs.** RFC 9728 places protected
   resource metadata at
   ``/.well-known/oauth-protected-resource`` + the resource's path, which is the
   *origin root* — a path the SDK's sub-application cannot own once it is
   mounted under ``/mcp``. This is the URL advertised in a ``401``
   ``WWW-Authenticate`` challenge, so it must be reachable.
2. **The consent decision.** The SDK redirects the browser to a URL we choose;
   these endpoints are what that page reads and posts to. The approval is bound
   to the *authenticated Transform user*, which is why it lives on the
   bearer-authenticated API rather than in the OAuth sub-application.
3. **Management.** Listing and revoking an application's access, so a user can
   withdraw consent without changing a password.

Security notes that shaped this file:

* Nothing here trusts a value from the consent page. ``approved_scopes`` is
  intersected with the scopes re-derived from the authorization request, so a
  tampered page cannot approve a capability the user was never shown, and the
  redirect URI is re-matched against the client's registered list by the
  service before a code is minted.
* Revocation is scoped by ``user_id`` in the repository, so a guessed grant id
  belonging to another account is reported exactly like a nonexistent one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.exceptions.mcp_exceptions import MCPAccessError
from src.application.services.mcp_access_service import MCPAccessService
from src.domain.security.value_object.agent_scope import (
    DESTRUCTIVE_SCOPES,
    SCOPE_DESCRIPTIONS,
    AgentScope,
    normalize_scopes,
)
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import get_mcp_access_service
from src.presentation.mcp.server import (
    build_authorization_server_metadata,
    build_protected_resource_metadata,
)
from src.presentation.schemas.mcp import (
    ConnectedAppListResponse,
    ConnectedAppResponse,
    ConsentApprovalRequest,
    ConsentApprovalResponse,
    ConsentRequestResponse,
    ScopeDescription,
)

#: Discovery endpoints are unauthenticated by necessity (a client must be able
#: to read them before it has any credential) and are excluded from the OpenAPI
#: schema, which documents the product API rather than the OAuth surface.
well_known_router = APIRouter(tags=["mcp"], include_in_schema=False)

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

MCPService = Annotated[MCPAccessService, Depends(get_mcp_access_service)]


# ---------------------------------------------------------------------------
# Discovery (RFC 9728 / RFC 8414)
# ---------------------------------------------------------------------------


@well_known_router.get("/.well-known/oauth-protected-resource", response_model=None)
@well_known_router.get("/.well-known/oauth-protected-resource/mcp", response_model=None)
async def protected_resource_metadata() -> dict:
    """RFC 9728 protected resource metadata for the MCP endpoint.

    Both the bare path and the resource-suffixed path are served: RFC 9728
    defines the suffixed form, and clients that normalise the well-known segment
    to the origin root request the bare one. The payload is identical, so either
    request answers correctly.
    """
    return build_protected_resource_metadata()


@well_known_router.get("/.well-known/oauth-authorization-server/mcp", response_model=None)
async def authorization_server_metadata_inserted_path() -> dict:
    """RFC 8414 authorization-server metadata at the *inserted* path.

    RFC 8414 §3.1 derives the metadata URL by inserting
    ``/.well-known/oauth-authorization-server`` before the issuer's path, giving
    ``/.well-known/oauth-authorization-server/mcp`` for an issuer of ``/mcp``.
    The SDK serves the concatenated form; both are returned here so either
    derivation resolves.
    """
    return build_authorization_server_metadata()


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def _scope_descriptions(
    requested: tuple[AgentScope, ...], already_granted: tuple[AgentScope, ...]
) -> list[ScopeDescription]:
    """Every scope the product understands, marked requested/granted.

    Listing all four (rather than only the requested ones) lets the consent
    screen explain what *isn't* being granted, which is how a user can tell
    that "delete my files" was not silently included.
    """
    return [
        ScopeDescription(
            scope=scope.value,
            description=SCOPE_DESCRIPTIONS[scope],
            requested=scope in set(requested),
            already_granted=scope in set(already_granted),
            destructive=scope in DESTRUCTIVE_SCOPES,
        )
        for scope in (AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_CONVERT,
                      AgentScope.DOCUMENTS_WRITE, AgentScope.DOCUMENTS_DELETE)
    ]


@router.get("/authorize", response_model=ConsentRequestResponse)
async def describe_consent_request(
    current_user: CurrentUser,
    access: MCPService,
    client_id: str,
    redirect_uri: str,
    scope: str = "",
    resource: str | None = None,
) -> ConsentRequestResponse:
    """Describe an authorization request for the consent screen.

    Every value is re-validated against the client's registration, so the page
    can only ever render a request that would in fact be approvable — the screen
    cannot show a scope that the approve call would then reject.
    """
    try:
        view = await access.describe_authorization(
            user_id=int(current_user.id),
            client_id=client_id,
            redirect_uri=redirect_uri,
            requested_scopes=scope.split() if scope else (),
            resource=resource,
        )
    except MCPAccessError as exc:
        # 404 for an unknown client, 400 for a bad request: the distinction is
        # already public (the client_id came from the browser), and MCP clients
        # surface both to the user as "the connection could not be started".
        code = status.HTTP_404_NOT_FOUND if "not registered" in exc.description else 400
        raise HTTPException(status_code=code, detail=exc.description) from exc
    return ConsentRequestResponse(
        client_id=view.client_id,
        client_name=view.client_name,
        redirect_uri=view.redirect_uri,
        resource=view.resource,
        scopes=_scope_descriptions(view.requested_scopes, view.already_granted),
    )


@router.post("/authorize", response_model=ConsentApprovalResponse)
async def approve_consent_request(
    current_user: CurrentUser,
    access: MCPService,
    body: ConsentApprovalRequest,
) -> ConsentApprovalResponse:
    """Record the user's approval and return the client's redirect URL.

    The approved set is intersected with the scopes re-derived from the original
    request, so the page cannot widen a consent beyond what it displayed; and
    the request itself (client, redirect URI, resource, PKCE challenge) is
    re-validated before any code is minted.
    """
    try:
        view = await access.describe_authorization(
            user_id=int(current_user.id),
            client_id=body.client_id,
            redirect_uri=body.redirect_uri,
            requested_scopes=body.scope.split() if body.scope else (),
            resource=body.resource,
        )
        approved = [
            scope
            for scope in normalize_scopes(body.approved_scopes)
            if scope in set(view.requested_scopes)
        ]
        if not approved:
            # Approving nothing is a refusal, not a zero-scope grant: a token
            # with no scopes could only ever produce confusing "permission
            # denied" answers later.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Select at least one permission to allow.",
            )
        redirect_url = await access.approve(
            user_id=int(current_user.id),
            client_id=body.client_id,
            redirect_uri=body.redirect_uri,
            requested_scopes=[scope.value for scope in approved],
            code_challenge=body.code_challenge,
            resource=body.resource,
            state=body.state,
        )
    except MCPAccessError as exc:
        code = status.HTTP_404_NOT_FOUND if "not registered" in exc.description else 400
        raise HTTPException(status_code=code, detail=exc.description) from exc
    return ConsentApprovalResponse(redirect_url=redirect_url)


# ---------------------------------------------------------------------------
# Connected applications
# ---------------------------------------------------------------------------


@router.get("/connected-apps", response_model=ConnectedAppListResponse)
async def list_connected_apps(
    current_user: CurrentUser, access: MCPService
) -> ConnectedAppListResponse:
    """List the applications the user has connected, active and revoked."""
    grants = await access.list_connections(int(current_user.id))
    return ConnectedAppListResponse(
        apps=[
            ConnectedAppResponse(
                id=grant.id,
                client_id=grant.client_id,
                client_name=grant.client_name,
                scopes=[scope.value for scope in grant.scopes],
                status=str(grant.status),
                created_at=grant.created_at,
                last_used_at=grant.last_used_at,
                revoked_at=grant.revoked_at,
            )
            for grant in grants
        ]
    )


@router.delete("/connected-apps/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_connected_app(
    grant_id: str, current_user: CurrentUser, access: MCPService
) -> Response:
    """Revoke one application's access immediately.

    Takes effect on the very next MCP request: access tokens are opaque and
    resolved through the (now revoked) grant, so there is no expiry window in
    which the agent keeps working. A grant belonging to another account is
    reported as not found, so the endpoint cannot be used to probe for ids.
    """
    grant = await access.revoke_connection(int(current_user.id), grant_id)
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
