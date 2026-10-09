"""Adapter binding the application's MCP access rules to the MCP SDK's OAuth.

The SDK ships a complete OAuth 2.1 authorization server (``/authorize``,
``/token``, ``/register``, ``/revoke`` and the RFC 8414 metadata document) but
leaves the *policy* to the application. This module is the seam: it converts
between the SDK's pydantic models and the application DTOs, and contains no
rules of its own.

Two deliberate properties:

* **No rule is re-implemented here.** Scope decisions, redirect-URI matching,
  resource (audience) binding, revocation and expiry live in
  :class:`~src.application.services.mcp_access_service.MCPAccessService`. If a
  check appears in this file, it is a bug — the adapter would then disagree with
  the service under some future edit.
* **A fresh database session per call.** OAuth endpoints and MCP tool calls are
  not FastAPI ``Depends``-built, so the session is scoped by
  ``session_scope`` — the same ``async_sessionmaker`` the API uses. A long-lived
  session would pin a pooled connection open for the process lifetime.

The class implements **both** SDK surfaces on purpose. The SDK only guards the
``/mcp`` route with authentication when a ``token_verifier`` is supplied, even
when an authorization server is configured — supplying one without the other
leaves the endpoint public, so they are intentionally the same object.
"""

import contextlib
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    OAuthClientInformationFull,
    RefreshToken,
    TokenError,
    TokenVerifier,
)
from mcp.shared.auth import OAuthToken
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exceptions.mcp_exceptions import MCPAccessError
from src.application.ports.mcp_oauth_port import OAuthClientRecord
from src.application.services.mcp_access_service import MCPAccessService, build_redirect_uri
from src.domain.security.value_object.agent_scope import ALL_SCOPES, AgentScope, scope_string
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository

#: How the adapter obtains a database session. Injected so tests can point it at
#: SQLite without reaching into module globals.
SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class MCPOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken],
    TokenVerifier,
):
    """The SDK-facing OAuth provider and token verifier for Transform's MCP."""

    def __init__(
        self,
        *,
        session_scope: SessionScope,
        resource_url: str,
        consent_url: str,
        access_token_ttl_minutes: int = 60,
        refresh_token_ttl_days: int = 30,
        authorization_code_ttl_minutes: int = 5,
        grantable_scopes: tuple[AgentScope, ...] = ALL_SCOPES,
    ) -> None:
        self._session_scope = session_scope
        self._resource_url = resource_url.rstrip("/")
        self._consent_url = consent_url
        self._access_token_ttl_minutes = access_token_ttl_minutes
        self._refresh_token_ttl_days = refresh_token_ttl_days
        self._authorization_code_ttl_minutes = authorization_code_ttl_minutes
        self._grantable_scopes = grantable_scopes

    # ------------------------------------------------------------------
    # Session / service plumbing
    # ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _service(self) -> AsyncGenerator[MCPAccessService]:
        async with self._session_scope() as session:
            yield MCPAccessService(
                repository=SQLMCPRepository(session),
                resource_url=self._resource_url,
                access_token_ttl_minutes=self._access_token_ttl_minutes,
                refresh_token_ttl_days=self._refresh_token_ttl_days,
                authorization_code_ttl_minutes=self._authorization_code_ttl_minutes,
                grantable_scopes=self._grantable_scopes,
            )

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    @staticmethod
    def _client_to_record(client: OAuthClientInformationFull) -> OAuthClientRecord:
        return OAuthClientRecord(
            client_id=client.client_id,
            client_secret=client.client_secret,
            client_name=client.client_name,
            redirect_uris=tuple(str(uri) for uri in (client.redirect_uris or ())),
            grant_types=tuple(client.grant_types or ()),
            response_types=tuple(client.response_types or ()),
            scope=client.scope,
            token_endpoint_auth_method=client.token_endpoint_auth_method,
        )

    @staticmethod
    def _record_to_client(record: OAuthClientRecord) -> OAuthClientInformationFull:
        return OAuthClientInformationFull(
            client_id=record.client_id,
            client_secret=record.client_secret,
            client_name=record.client_name,
            redirect_uris=list(record.redirect_uris),
            grant_types=list(record.grant_types) or ["authorization_code", "refresh_token"],
            response_types=list(record.response_types) or ["code"],
            scope=record.scope,
            token_endpoint_auth_method=record.token_endpoint_auth_method,
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._service() as service:
            record = await service.get_client(client_id)
        return self._record_to_client(record) if record is not None else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with self._service() as service:
            await service.register_client(self._client_to_record(client_info))

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Return the URL the browser must be sent to.

        That URL is the **SPA's consent screen**, not the client's redirect
        target: the signed-in session lives in the SPA as a bearer token, so a
        top-level navigation to the API could not carry it. The screen shows the
        request and posts an approval back to the API, which is the only place
        the code is actually minted.

        Scope and resource are validated *here* as well so an impossible request
        fails before a user is ever shown a consent screen for it.
        """
        async with self._service() as service:
            record = OAuthClientRecord(
                client_id=client.client_id,
                redirect_uris=tuple(str(uri) for uri in (client.redirect_uris or ())),
                scope=client.scope,
            )
            try:
                scopes = service.resolve_scopes(record, params.scopes)
                resource = service.validate_resource(params.resource)
            except MCPAccessError as exc:
                raise AuthorizeError(
                    error=cast(Any, exc.error), error_description=exc.description
                ) from exc

        return build_redirect_uri(
            self._consent_url,
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            scope=scope_string(scopes),
            state=params.state,
            code_challenge=params.code_challenge,
            code_challenge_method="S256",
            resource=resource,
        )

    # ------------------------------------------------------------------
    # Authorization code grant
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """Read a code (without consuming it) for the token endpoint's checks.

        Consumption is atomic and happens once, in
        :meth:`exchange_authorization_code`. Returning ``None`` for an already
        used code is what makes a replay look like a nonexistent code.
        """
        async with self._service() as service:
            record = await service.peek_authorization_code(authorization_code)
        if record is None or record.used_at is not None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=record.client_id,
            redirect_uri=record.redirect_uri,
            redirect_uri_provided_explicitly=True,
            scopes=[scope.value for scope in record.scopes],
            expires_at=record.expires_at.timestamp(),
            code_challenge=record.code_challenge,
            resource=record.resource,
            subject=record.subject,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with self._service() as service:
            try:
                issued = await service.exchange_code(
                    client_id=client.client_id,
                    code=authorization_code.code,
                    # The SDK has already enforced that the redirect_uri matches
                    # the one used at /authorize (RFC 6749 §10.6), and it hands
                    # us a pydantic-normalised URL that would not string-compare
                    # equal to the registered value. Re-checking here would
                    # reject valid requests, so the SDK's check stands.
                    redirect_uri=None,
                    resource=authorization_code.resource,
                )
            except MCPAccessError as exc:
                raise self._token_error(exc) from exc
        return self._to_oauth_token(issued)

    # ------------------------------------------------------------------
    # Refresh token grant
    # ------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with self._service() as service:
            record = await service.peek_refresh_token(refresh_token)
        if record is None or record.kind != "REFRESH" or record.revoked_at is not None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=[scope.value for scope in record.scopes],
            expires_at=int(record.expires_at.timestamp()),
            resource=record.resource,
            subject=record.subject,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        async with self._service() as service:
            try:
                issued = await service.exchange_refresh(
                    client_id=client.client_id,
                    refresh_token=refresh_token.token,
                    scopes=scopes,
                    resource=refresh_token.resource,
                )
            except MCPAccessError as exc:
                raise self._token_error(exc) from exc
        return self._to_oauth_token(issued)

    # ------------------------------------------------------------------
    # Access tokens (also the TokenVerifier contract)
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        async with self._service() as service:
            record = await service.load_access_token(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=[scope.value for scope in record.scopes],
            expires_at=int(record.expires_at.timestamp()),
            resource=record.resource,
            subject=record.subject,
            claims={"grant_id": record.grant_id},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """``TokenVerifier`` entry point — identical to ``load_access_token``.

        The SDK needs a verifier to wrap the ``/mcp`` route in
        ``RequireAuthMiddleware``; routing both through one method guarantees
        the MCP endpoint and the token endpoint can never disagree about whether
        a token is good.
        """
        return await self.load_access_token(token)

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with self._service() as service:
            await service.revoke_token(token.token)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _token_error(exc: MCPAccessError) -> TokenError:
        return TokenError(error=cast(Any, exc.error), error_description=exc.description)

    @staticmethod
    def _to_oauth_token(issued: Any) -> OAuthToken:
        return OAuthToken(
            access_token=issued.access_token,
            token_type="Bearer",
            expires_in=issued.expires_in,
            scope=scope_string(issued.scopes),
            refresh_token=issued.refresh_token,
        )
