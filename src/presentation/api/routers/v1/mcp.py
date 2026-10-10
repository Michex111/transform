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

from src.application.exceptions.file_system_exceptions import (
    FileSystemError,
    FolderNameConflictError,
)
from src.application.exceptions.mcp_exceptions import ConnectionStateError, MCPAccessError
from src.application.services.file_service import FileService
from src.application.services.mcp_access_service import MCPAccessService
from src.domain.security.enitities.agent_grant import AgentGrant
from src.domain.security.value_object.agent_access_scope import FolderAccess
from src.domain.security.value_object.agent_scope import (
    DESTRUCTIVE_SCOPES,
    SCOPE_DESCRIPTIONS,
    AgentScope,
    normalize_scopes,
)
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_file_service,
    get_mcp_access_service,
)
from src.presentation.mcp.server import (
    build_authorization_server_metadata,
    build_protected_resource_metadata,
)
from src.presentation.schemas.mcp import (
    ConnectedAppListResponse,
    ConnectedAppResponse,
    ConsentApprovalRequest,
    ConsentApprovalResponse,
    ConsentFolderOption,
    ConsentRequestResponse,
    FolderAccessEntry,
    FolderAccessResponse,
    ScopeDescription,
    UpdateConnectedAppRequest,
)

#: Discovery endpoints are unauthenticated by necessity (a client must be able
#: to read them before it has any credential) and are excluded from the OpenAPI
#: schema, which documents the product API rather than the OAuth surface.
well_known_router = APIRouter(tags=["mcp"], include_in_schema=False)

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

MCPService = Annotated[MCPAccessService, Depends(get_mcp_access_service)]
FileServiceDep = Annotated[FileService, Depends(get_file_service)]

#: Upper bound on the folders the consent screen offers.
#:
#: The consent screen is a picker, not a file browser, and a large Drive can
#: hold thousands of folders. Listing them all would make the response (and the
#: query behind it) unbounded for the largest accounts, so the router caps the
#: list at this many root folders — enough that a typical user sees everything,
#: and the screen can still add a search or an explicit "new folder" affordance
#: for the rest without the endpoint ever growing with the account.
_MAX_CONSENT_FOLDERS = 100

#: How many root folders the new-folder duplicate-name check scans. The picker
#: only shows ``_MAX_CONSENT_FOLDERS``, so a folder beyond this window is not
#: offered to the user in the first place; scanning further would make consent
#: unbounded.
#:
#: The check exists because the sibling-uniqueness constraint cannot be relied on
#: on its own here: it is keyed on ``(user_id, parent_id, name)``, and both
#: SQLite and PostgreSQL treat a NULL ``parent_id`` as distinct — so the database
#: would happily allow two root folders with the same name. A consent flow that
#: always creates at the root therefore needs its own look.
_MAX_FOLDER_NAME_SCAN = 500


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


async def _resolve_consent_folder(
    file_service: FileService,
    user_id: int,
    *,
    folder_access: FolderAccess,
    folder_id: str | None,
    new_folder_name: str | None,
) -> str | None:
    """Resolve the folder a consent should bind, performing the folder I/O.

    Lives in the router (not the access service) because it needs ``FileService``
    and the access service is deliberately kept free of file/folder logic. The
    returned id is the only thing the service persists; every path here has
    already proven the folder is the caller's.

    Returns ``None`` for whole-Drive consent. Raises ``HTTPException`` with an
    actionable detail for anything a user can fix.
    """
    if folder_access is FolderAccess.ALL:
        # Whole-Drive consent carries no folder binding. A folder id supplied
        # alongside it is discarded so a tampered page cannot bind a folder
        # while claiming full access (constraining an agent is the user's
        # choice, never the page's).
        return None

    if new_folder_name is not None:
        name = new_folder_name.strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a name for the new folder.",
            )
        existing, _total = await file_service.list_root_folders(
            user_id, offset=0, limit=_MAX_FOLDER_NAME_SCAN,
        )
        if any(folder.name == name for folder in existing):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"A folder named '{name}' already exists. "
                    "Choose a different name, or pick that folder instead."
                ),
            )
        try:
            folder = await file_service.create_folder(user_id, name)
        except FolderNameConflictError as exc:
            # The files router surfaces this as 409; on the consent screen a
            # duplicate is a field-level mistake the user can immediately
            # correct, so it is a 400 with a sentence that says what to do.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"A folder named '{name}' already exists. "
                    "Choose a different name, or pick that folder instead."
                ),
            ) from exc
        except FileSystemError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
        return folder.id

    if folder_id:
        try:
            folder = await file_service.get_folder(user_id, folder_id)
        except FileSystemError as exc:
            # get_folder raises FolderNotFoundError (404) for a folder that is
            # missing *or* owned by somebody else, so a foreign id is
            # indistinguishable from a nonexistent one — no ownership oracle.
            # Surface it as the error's own status (404), never a 500.
            raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc
        return folder.id

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Choose a folder, or allow access to your whole Drive.",
    )


@router.get("/authorize", response_model=ConsentRequestResponse)
async def describe_consent_request(
    current_user: CurrentUser,
    access: MCPService,
    file_service: FileServiceDep,
    client_id: str,
    redirect_uri: str,
    scope: str = "",
    resource: str | None = None,
) -> ConsentRequestResponse:
    """Describe an authorization request for the consent screen.

    Every value is re-validated against the client's registration, so the page
    can only ever render a request that would in fact be approvable — the screen
    cannot show a scope that the approve call would then reject. Alongside the
    scopes it reports the user's current binding (folder and history scope) and
    the folders they may choose, so the screen reflects the existing decision.
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

    folder_rows, _total = await file_service.list_root_folders(
        int(current_user.id), offset=0, limit=_MAX_CONSENT_FOLDERS,
    )
    return ConsentRequestResponse(
        client_id=view.client_id,
        client_name=view.client_name,
        redirect_uri=view.redirect_uri,
        resource=view.resource,
        scopes=_scope_descriptions(view.requested_scopes, view.already_granted),
        folders=[
            ConsentFolderOption(folder_id=folder.id, name=folder.name)
            for folder in folder_rows
        ],
        folder_access=view.folder_access,
        folder_id=view.folder_id,
        history_scope=view.history_scope,
        can_choose_history_scope=True,
    )


@router.post("/authorize", response_model=ConsentApprovalResponse)
async def approve_consent_request(
    current_user: CurrentUser,
    access: MCPService,
    file_service: FileServiceDep,
    body: ConsentApprovalRequest,
) -> ConsentApprovalResponse:
    """Record the user's approval and return the client's redirect URL.

    The approved set is intersected with the scopes re-derived from the original
    request, so the page cannot widen a consent beyond what it displayed; and
    the request itself (client, redirect URI, resource, PKCE challenge) is
    re-validated before any code is minted. The folder binding is resolved
    against the user's own folders here, before the grant is persisted.
    """
    user_id = int(current_user.id)
    try:
        view = await access.describe_authorization(
            user_id=user_id,
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
    except MCPAccessError as exc:
        code = status.HTTP_404_NOT_FOUND if "not registered" in exc.description else 400
        raise HTTPException(status_code=code, detail=exc.description) from exc

    resolved_folder_id = await _resolve_consent_folder(
        file_service,
        user_id,
        folder_access=body.folder_access,
        folder_id=body.folder_id,
        new_folder_name=body.new_folder_name,
    )

    try:
        redirect_url = await access.approve(
            user_id=user_id,
            client_id=body.client_id,
            redirect_uri=body.redirect_uri,
            requested_scopes=[scope.value for scope in approved],
            code_challenge=body.code_challenge,
            resource=body.resource,
            state=body.state,
            folder_access=body.folder_access,
            folder_id=resolved_folder_id,
            history_scope=body.history_scope,
        )
    except MCPAccessError as exc:
        code = status.HTTP_404_NOT_FOUND if "not registered" in exc.description else 400
        raise HTTPException(status_code=code, detail=exc.description) from exc
    return ConsentApprovalResponse(redirect_url=redirect_url)


@router.get("/folder-access", response_model=FolderAccessResponse)
async def list_active_folder_access(
    current_user: CurrentUser, access: MCPService
) -> FolderAccessResponse:
    """Folders currently bound by one of the authenticated user's active agents.

    The Files page uses this to mark the folders an authorized AI agent is
    allowed to work in. Every grant is read for the *authenticated user only*
    (``list_grants`` filters by ``user_id``), then narrowed to active grants
    whose consent is folder-confined with a usable folder id — the same
    condition the toolbox enforces, so the badge can never advertise a binding
    that grants nothing.
    """
    grants = await access.list_connections(int(current_user.id))
    folders: list[FolderAccessEntry] = []
    for grant in grants:
        folder_id = grant.folder_id
        if folder_id and grant.is_active() and grant.folder_access is FolderAccess.FOLDER:
            folders.append(
                FolderAccessEntry(
                    folder_id=folder_id,
                    client_name=grant.client_name,
                    grant_id=grant.id,
                )
            )
    return FolderAccessResponse(folders=folders)


# ---------------------------------------------------------------------------
# Connected applications
# ---------------------------------------------------------------------------


async def _resolve_folder_name(
    file_service: FileService, user_id: int, folder_id: str | None
) -> str | None:
    """The display name of a bound folder, or ``None`` when it cannot be read.

    A grant may legitimately outlive its folder — deleting a folder does not
    delete the consent — so a missing folder is reported as a missing *name*
    rather than an error: the Connected-apps list must still render the
    connection. Enforcement is what denies the unusable binding, not this.
    """
    if not folder_id:
        return None
    try:
        folder = await file_service.get_folder(user_id, folder_id)
    except FileSystemError:
        return None
    return folder.name


def _connected_app_response(
    grant: AgentGrant, *, folder_name: str | None
) -> ConnectedAppResponse:
    """Render a grant for the management API, populating the binding fields."""
    return ConnectedAppResponse(
        id=grant.id,
        client_id=grant.client_id,
        client_name=grant.client_name,
        scopes=[scope.value for scope in grant.scopes],
        status=str(grant.status),
        created_at=grant.created_at,
        last_used_at=grant.last_used_at,
        revoked_at=grant.revoked_at,
        folder_access=grant.folder_access,
        folder_id=grant.folder_id,
        folder_name=folder_name,
        history_scope=grant.history_scope,
    )


@router.get("/connected-apps", response_model=ConnectedAppListResponse)
async def list_connected_apps(
    current_user: CurrentUser, access: MCPService, file_service: FileServiceDep
) -> ConnectedAppListResponse:
    """List the applications the user has connected, active and revoked.

    Each entry carries the connection's folder binding and history scope, so the
    Settings screen can display them and pre-fill an in-place edit. The bound
    folder's display name is resolved here; a folder that no longer exists is
    reported as ``folder_name: null`` rather than failing the listing.
    """
    user_id = int(current_user.id)
    grants = await access.list_connections(user_id)
    apps: list[ConnectedAppResponse] = []
    for grant in grants:
        folder_name = await _resolve_folder_name(file_service, user_id, grant.folder_id)
        apps.append(_connected_app_response(grant, folder_name=folder_name))
    return ConnectedAppListResponse(apps=apps)


@router.patch("/connected-apps/{grant_id}", response_model=ConnectedAppResponse)
async def update_connected_app(
    grant_id: str,
    body: UpdateConnectedAppRequest,
    current_user: CurrentUser,
    access: MCPService,
    file_service: FileServiceDep,
) -> ConnectedAppResponse:
    """Edit one application's permissions in place, without re-consenting.

    Replaces the connection's scope set, folder binding and history scope in a
    single call. Unlike the OAuth consent flow it never restarts a token
    exchange, and it never changes the connection's **status** — editing a
    permission is not the same act as resuming, so a paused connection stays
    paused, and a revoked connection cannot be edited at all (409).

    What the UI should tell the user about the agent's behaviour:

    * **Narrowing takes effect on the agent's next call.** Every MCP request
      re-reads the grant and ``MCPAccessService.load_access_token`` refuses any
      token whose scopes are no longer a subset of the grant's — so the agent's
      existing token stops working immediately and it must re-authorize.
    * **Widening does not retroactively upgrade an already-issued token.** The
      agent gains a newly-added permission when it next authorizes (or refreshes
      its token), not before.
    * Editing never changes whether the connection is paused or revoked.

    The folder binding is resolved against the user's own folders before the
    service is called — the same rule the consent approval follows — so a folder
    id the caller does not own is refused rather than stored, and a whole-Drive
    edit discards any supplied folder id.
    """
    user_id = int(current_user.id)

    # Resolve the folder in the router (it owns file I/O), so the service only
    # ever receives an id that is provably the caller's.
    resolved_folder_id: str | None = None
    folder_name: str | None = None
    if body.folder_access is FolderAccess.FOLDER:
        if not body.folder_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Choose a folder for this connection, "
                    "or allow access to your whole Drive."
                ),
            )
        try:
            folder = await file_service.get_folder(user_id, body.folder_id)
        except FileSystemError as exc:
            # get_folder reports a missing folder and another user's folder the
            # same way, so this is not an ownership oracle. Surfaced as a 400
            # with a fixable sentence rather than the error's own 404: the
            # folder field is what the user must correct, not the connection.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "That folder was not found in your Drive. "
                    "Choose one of your own folders."
                ),
            ) from exc
        resolved_folder_id = folder.id
        folder_name = folder.name
    # Whole-Drive: any supplied folder id is discarded, so a request cannot
    # claim unrestricted access while binding a folder.

    try:
        grant = await access.update_connected_app(
            user_id=user_id,
            grant_id=grant_id,
            scopes=body.scopes,
            folder_access=body.folder_access,
            folder_id=resolved_folder_id,
            history_scope=body.history_scope,
            confirm_destructive=body.confirm_destructive,
        )
    except ConnectionStateError as exc:
        # A revoked connection is the caller's but terminal — 409, not 404.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.description) from exc
    except MCPAccessError as exc:
        # Empty/unknown scope sets and an unconfirmed destructive addition are
        # all requests the user can correct, so they are 400s.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.description) from exc

    if grant is None:
        # Not-found and not-owned are the same answer, on purpose: a distinct
        # 403 would confirm that somebody else's grant id exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    return _connected_app_response(grant, folder_name=folder_name)


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
