"""Persistence contract for the MCP (agent access) OAuth server.

The application layer owns the OAuth *rules* (scope decisions, PKCE-adjacent
binding, expiry, revocation); the repository owns *storage*. Keeping the port in
the application layer — and expressed in application DTOs rather than the MCP
SDK's models — is deliberate: ``src/application`` must not depend on a transport
SDK, so the SDK's pydantic models are converted at the infrastructure adapter.

Credential material never crosses this boundary in plaintext. The service hashes
codes and tokens before they reach the port, so a repository (or a database
dump) holds nothing that can be replayed. Every method that takes a credential
therefore takes a *hash*, and the naming (``code_hash`` / ``token_hash``) makes
that hard to miss.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from src.domain.security.enitities.agent_grant import AgentGrant
from src.domain.security.value_object.agent_scope import AgentScope


@dataclass(frozen=True)
class OAuthClientRecord:
    """A dynamically registered MCP client, in application terms.

    ``redirect_uris`` is a list rather than a single value because the OAuth
    spec allows several. It is the *only* place a redirect target may come from:
    an authorization response is never sent to a URI supplied in the request
    alone (that is the classic open-redirect in an OAuth server).
    """

    client_id: str
    client_secret: str | None = None
    client_name: str | None = None
    redirect_uris: Sequence[str] = field(default_factory=tuple)
    grant_types: Sequence[str] = field(default_factory=tuple)
    response_types: Sequence[str] = field(default_factory=tuple)
    scope: str | None = None
    token_endpoint_auth_method: str | None = None


@dataclass(frozen=True)
class AuthorizationCodeRecord:
    """A single-use PKCE authorization code, identified by its hash."""

    code_hash: str
    grant_id: str
    client_id: str
    subject: str
    scopes: tuple[AgentScope, ...]
    code_challenge: str
    redirect_uri: str
    resource: str | None
    expires_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True)
class TokenRecord:
    """An issued access or refresh token, identified by its hash.

    ``kind`` is the domain string (``"ACCESS"`` / ``"REFRESH"``) so the
    application layer does not import the ORM's enum.
    """

    token_hash: str
    grant_id: str
    client_id: str
    subject: str
    kind: str
    scopes: tuple[AgentScope, ...]
    expires_at: datetime
    resource: str | None = None
    revoked_at: datetime | None = None


class MCPRepositoryPort(Protocol):
    """Storage for MCP clients, grants, authorization codes and tokens."""

    # -- Clients ------------------------------------------------------
    async def save_client(self, client: OAuthClientRecord) -> None: ...

    async def get_client(self, client_id: str) -> OAuthClientRecord | None: ...

    # -- Grants -------------------------------------------------------
    async def upsert_grant(self, grant: AgentGrant) -> AgentGrant:
        """Insert or reactivate the (user, client) grant and return it."""
        ...

    async def get_grant(self, grant_id: str) -> AgentGrant | None: ...

    async def get_grant_for_user_client(self, user_id: int, client_id: str) -> AgentGrant | None: ...

    async def list_grants(self, user_id: int) -> list[AgentGrant]: ...

    async def revoke_grant(self, grant_id: str, user_id: int) -> AgentGrant | None: ...

    async def touch_grant(self, grant_id: str, used_at: datetime) -> None: ...

    # -- Authorization codes -----------------------------------------
    async def save_code(self, code: AuthorizationCodeRecord) -> None: ...

    async def peek_code(self, code_hash: str) -> AuthorizationCodeRecord | None:
        """Look a code up **without** consuming it.

        Needed because the token endpoint validates a code (client, expiry,
        redirect URI, PKCE) before redeeming it, and those validations must not
        burn the code: a failed PKCE check has to leave the code unusable-by-
        anyone but must not be retried into a success. Consumption happens
        exactly once, in :meth:`take_code`.
        """
        ...

    async def take_code(self, code_hash: str, now: datetime) -> AuthorizationCodeRecord | None:
        """Return an unused, unexpired code and mark it used, atomically.

        Atomicity matters: a code that is read and then marked used in two
        steps can be redeemed twice by two concurrent requests.
        """
        ...

    # -- Tokens -------------------------------------------------------
    async def save_token(self, token: TokenRecord) -> None: ...

    async def get_token(self, token_hash: str) -> TokenRecord | None: ...

    async def revoke_token(self, token_hash: str, revoked_at: datetime) -> None: ...

    async def revoke_tokens_for_grant(self, grant_id: str, revoked_at: datetime) -> int: ...

    async def delete_expired(self, before: datetime) -> int:
        """Purge spent codes and expired tokens (housekeeping)."""
        ...
