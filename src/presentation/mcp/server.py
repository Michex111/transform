"""Builds the Transform MCP server and its ASGI application.

The server is an OAuth **resource server AND authorization server** at once.
That is a supported (and common) MCP topology: the same service issues the
tokens and accepts them, which keeps the token issuer and the audience under one
operator and avoids a second identity system next to the one Transform already
has.

Configuration that is load-bearing rather than cosmetic:

* ``auth_server_provider`` is supplied, and the SDK derives the route's token
  verifier from it (``ProviderTokenVerifier``), which is what makes the
  ``/mcp`` route authenticated. Supplying neither — or only a verifier — would
  leave the endpoint publicly callable or the OAuth endpoints missing. Because
  one object fills both roles, the token endpoint and the MCP endpoint can never
  disagree about whether a token is good.
* ``validate_token_resource=True`` enforces the RFC 8707 audience check, so a
  token minted for any other resource is rejected even if it is otherwise valid.
* ``required_scopes=[]`` — scope requirements are **per tool**, not per route. A
  route-level requirement would force every caller to hold every scope, which is
  exactly the over-granting the scope split exists to prevent. The advertised
  scope list therefore lives in the protected-resource metadata instead.
* ``stateless_http=True``. The tools here never use the server→client
  back-channel (sampling, elicitation, roots), so dropping the transport session
  costs nothing and removes the need for sticky sessions or a shared session
  store — the API can be scaled to any number of instances behind a plain load
  balancer.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.routes import (
    ProtectedResourceMetadata,
    build_metadata,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from src.domain.security.value_object.agent_scope import KNOWN_REQUEST_SCOPES
from src.infrastructure.adapters.security.mcp_oauth_provider import MCPOAuthProvider
from src.infrastructure.config.settings import get_settings
from src.presentation.mcp.dependencies import get_mcp_oauth_provider
from src.presentation.mcp.tools import INSTRUCTIONS, register_tools

#: Path the MCP transport is mounted at on the API. Clients are configured with
#: ``<api origin>/mcp``.
MCP_MOUNT_PATH = "/mcp"


def client_registration_options() -> ClientRegistrationOptions:
    """Dynamic client registration settings, shared by both metadata documents."""
    return ClientRegistrationOptions(
        enabled=True,
        # The server refuses to register a client for a scope it cannot grant,
        # so a client cannot ask for a capability that does not exist and then
        # be surprised at token time.
        #
        # Advertised as the full REQUEST vocabulary rather than ``ALL_SCOPES``,
        # because ``offline_access`` is a marker a client legitimately registers
        # for and requesting it here is not an error. It is still never granted:
        # the resolver drops markers before the capability set is built.
        valid_scopes=list(KNOWN_REQUEST_SCOPES),
        # ``default_scopes`` fills in a client's ``scope`` when it registers
        # WITHOUT one (see the SDK's register handler), and that value is then
        # treated as the client's own cap: the consent endpoint refuses any
        # scope outside it. It therefore must be the FULL set, not the consent
        # default.
        #
        # Using DEFAULT_SCOPES here was a real defect: most MCP clients omit
        # ``scope`` at registration (RFC 7591 makes it optional) and rely on
        # ``scopes_supported`` discovery, so they were silently capped to
        # read/convert/write. ``documents.delete`` and both ``workspaces.*``
        # scopes then became impossible to grant — the consent screen refused
        # them as "outside the client's registered scope", even though the
        # server advertises them and 10 of its 20 tools require them.
        #
        # The genuinely conservative default still applies where it belongs: an
        # authorization request that names no scope resolves to
        # ``DEFAULT_SCOPES`` in ``MCPAccessService.resolve_scopes``.
        default_scopes=list(KNOWN_REQUEST_SCOPES),
    )


def build_protected_resource_metadata() -> dict[str, Any]:
    """RFC 9728 protected-resource metadata for this MCP endpoint.

    Served at the canonical ``/.well-known/oauth-protected-resource/mcp`` by the
    API. The SDK also embeds a copy inside its sub-application, but mounted at
    ``/mcp`` that copy lands on a nested path, so the URL advertised in a
    ``WWW-Authenticate`` challenge has to be served from the origin root.
    """
    settings = get_settings()
    return ProtectedResourceMetadata(
        resource=AnyHttpUrl(settings.mcp_resource_url()),
        authorization_servers=[AnyHttpUrl(settings.mcp_issuer_url())],
        # Scope requirements are per tool, so this is the *advertised* set
        # rather than a route-level requirement (see the module docstring).
        #
        # This includes ``offline_access`` on purpose. It is a request marker,
        # not a capability, but clients decide whether to attempt a refresh by
        # looking for it here — absent, they conclude refresh is unavailable and
        # drop the connection when the 60-minute access token expires, even
        # though a refresh token was issued and works.
        scopes_supported=list(KNOWN_REQUEST_SCOPES),
        resource_name="Transform File Converter",
    ).model_dump(mode="json", exclude_none=True)


def build_authorization_server_metadata() -> dict[str, Any]:
    """RFC 8414 authorization-server metadata, built by the SDK itself.

    Built through ``build_metadata`` rather than hand-written so the document is
    byte-for-byte the one the SDK serves at
    ``{issuer}/.well-known/oauth-authorization-server``. This copy exists only
    because RFC 8414 lets a client derive the metadata URL by *inserting* the
    well-known segment before the issuer's path
    (``/.well-known/oauth-authorization-server/mcp``); serving both forms means
    either derivation succeeds.
    """
    settings = get_settings()
    return build_metadata(
        AnyHttpUrl(settings.mcp_issuer_url()),
        None,
        client_registration_options(),
        RevocationOptions(enabled=True),
    ).model_dump(mode="json", exclude_none=True)


def create_mcp_server(provider: MCPOAuthProvider | None = None) -> MCPServer:
    """Build the MCP server with its tools and OAuth configuration.

    ``provider`` is injectable so a test can bind the server to its own
    database instead of rebuilding (and drifting from) the production wiring.
    """
    settings = get_settings()
    provider = provider or get_mcp_oauth_provider()

    server = MCPServer(
        name="transform",
        title="Transform File Converter",
        version="1.0.0",
        instructions=INSTRUCTIONS,
        # The provider fills both roles: the SDK derives the token verifier from
        # it (ProviderTokenVerifier), so this single object mints and validates
        # tokens and the two endpoints cannot disagree about a token's validity.
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.mcp_issuer_url()),
            resource_server_url=AnyHttpUrl(settings.mcp_resource_url()),
            # Per-tool scopes; see the module docstring.
            required_scopes=[],
            client_registration_options=client_registration_options(),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=True,
        ),
    )
    register_tools(server)
    return server


def build_mcp_asgi_app(server: MCPServer) -> Any:
    """The Starlette sub-application to mount on the API.

    ``streamable_http_path="/"`` places the transport at the mount point itself,
    so the public endpoint is ``/mcp`` rather than ``/mcp/mcp``.

    DNS-rebinding protection is disabled **explicitly**. The SDK switches it on
    by default with an allowlist of ``localhost``/``127.0.0.1`` origins, which
    is the right posture for a developer's stdio-adjacent local server — and
    catastrophically wrong here, where it would answer every internet request
    with ``421 Misdirected Request`` because the Host is a real domain. It would
    also buy nothing: the attack it defends against is a browser on the user's
    machine being tricked into reaching a server that trusts *ambient* (cookie)
    credentials, whereas every MCP request here must carry an OAuth bearer token
    bound to this exact audience.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class LazyMCPMount:
    """ASGI app that forwards to an MCP sub-app created for the current lifespan.

    WHY the server is not built at import time: the Streamable HTTP session
    manager may only be started **once per instance**, so an import-time server
    makes the whole application single-use — which breaks any test suite and is
    a latent trap for anything that starts the ASGI app twice (a second worker
    in one process, a graceful reload). Building it inside the lifespan ties the
    session manager to the process it actually serves.
    """

    def __init__(self) -> None:
        self._app: Any | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[None]:
        """Build the server, start its session manager, and clear it afterwards."""
        server = create_mcp_server()
        self._app = build_mcp_asgi_app(server)
        try:
            async with server.session_manager.run():
                yield
        finally:
            # Cleared so a request that arrives outside the lifespan gets an
            # explicit error instead of a half-dead transport.
            self._app = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if self._app is None:
            raise RuntimeError("The MCP transport is not running.")
        await self._app(scope, receive, send)
