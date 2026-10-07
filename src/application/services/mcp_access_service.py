"""The MCP authorization server's rules: grants, codes, tokens and scopes.

This is the application layer for **AI agent access**. It answers four
questions, and nothing else in the codebase answers them:

1. *May this application act for this user, and for what?* — the
   :class:`~src.domain.security.enitities.agent_grant.AgentGrant` row, created
   only by an explicit approval from a signed-in user.
2. *Which capabilities did the user actually consent to?* — :meth:`resolve_scopes`,
   the single place a scope set is decided. It fails closed on any unrecognised
   value rather than silently narrowing to a default (a silent narrowing is a
   silent widening of whatever the default happens to be).
3. *Is this token still good?* — :meth:`load_access_token`, which re-reads the
   grant on **every** call so revoking on the Connected-apps screen takes effect
   immediately rather than after a token expiry window.
4. *Is this token for us?* — :meth:`validate_resource` implements the RFC 8707
   resource-indicator check. The MCP specification forbids a server from
   accepting (or forwarding) a token issued for a different resource, so a
   mismatch is an error, not a warning.

Deliberately absent: any file, conversion or storage logic. Those live in the
services this layer is a gate in front of.
"""

import hashlib
import secrets
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.application.exceptions.mcp_exceptions import (
    GrantRevokedError,
    InvalidGrantError,
    InvalidRedirectUriError,
    InvalidScopeError,
    InvalidTargetError,
    UnknownClientError,
)
from src.application.ports.mcp_oauth_port import (
    AuthorizationCodeRecord,
    MCPRepositoryPort,
    OAuthClientRecord,
    TokenRecord,
)
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.value_object.agent_scope import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    AgentScope,
    is_subset,
    normalize_scopes,
)

#: Token discriminators. Plain strings so the application layer stays free of
#: the ORM's enum while the repository can persist them directly.
ACCESS_TOKEN_KIND = "ACCESS"
REFRESH_TOKEN_KIND = "REFRESH"

_KNOWN_SCOPE_VALUES = frozenset(scope.value for scope in ALL_SCOPES)


def hash_secret(value: str) -> str:
    """SHA-256 hex digest of a credential, for storage.

    SHA-256 (not bcrypt) is the right choice here: these are 256-bit random
    values, not user-chosen passwords, so there is no dictionary to attack and
    the lookup must stay a fast unique-index hit on every MCP request.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_redirect_uri(base: str, **params: str | None) -> str:
    """Append ``params`` (dropping ``None``) to ``base``, preserving its query.

    A registered redirect URI may legitimately carry its own query string (the
    spec allows it), so the existing parameters are kept and the OAuth
    response parameters are added to them.
    """
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update({key: value for key, value in params.items() if value is not None})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(frozen=True)
class IssuedTokens:
    """The result of redeeming a code or a refresh token."""

    access_token: str
    expires_in: int
    scopes: tuple[AgentScope, ...]
    refresh_token: str | None = None


@dataclass(frozen=True)
class AuthorizationRequestView:
    """What the consent screen needs in order to render a decision.

    Contains no credential: every field is either public (``client_id``,
    ``redirect_uri``) or derived from configuration. The consent page never
    receives a code or a token — those are minted only after an approval.
    """

    client_id: str
    client_name: str
    redirect_uri: str
    resource: str
    requested_scopes: tuple[AgentScope, ...]
    #: Scopes this user already granted this client, so the screen can say
    #: "already allowed" instead of pretending the decision is new.
    already_granted: tuple[AgentScope, ...]


class MCPAccessService:
    """Authorization-server rules for MCP (agent) access."""

    def __init__(
        self,
        *,
        repository: MCPRepositoryPort,
        resource_url: str,
        access_token_ttl_minutes: int = 60,
        refresh_token_ttl_days: int = 30,
        authorization_code_ttl_minutes: int = 5,
        grantable_scopes: Sequence[AgentScope] = ALL_SCOPES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        #: The RFC 8707 audience this server accepts and mints for. Trailing
        #: slashes are normalised away so ``/mcp`` and ``/mcp/`` cannot be used
        #: to smuggle a token past the audience check.
        self._resource = resource_url.rstrip("/")
        self._access_ttl = timedelta(minutes=access_token_ttl_minutes)
        self._refresh_ttl = timedelta(days=refresh_token_ttl_days)
        self._code_ttl = timedelta(minutes=authorization_code_ttl_minutes)
        self._grantable = tuple(grantable_scopes)
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # Clients (dynamic registration)
    # ------------------------------------------------------------------

    async def register_client(self, client: OAuthClientRecord) -> OAuthClientRecord:
        """Persist a newly registered client. Idempotent on ``client_id``."""
        await self._repository.save_client(client)
        return client

    async def get_client(self, client_id: str) -> OAuthClientRecord | None:
        return await self._repository.get_client(client_id)

    # ------------------------------------------------------------------
    # Authorization (consent)
    # ------------------------------------------------------------------

    async def describe_authorization(
        self,
        *,
        user_id: int,
        client_id: str,
        redirect_uri: str,
        requested_scopes: Iterable[str] | None,
        resource: str | None,
    ) -> AuthorizationRequestView:
        """Validate an authorization request and describe it for the consent screen.

        Runs the *same* checks as :meth:`approve` so a request that the consent
        screen renders cannot later be rejected — or, worse, approved with
        values the screen never showed.
        """
        client = await self._require_client(client_id)
        self._ensure_redirect_uri(client, redirect_uri)
        scopes = self.resolve_scopes(client, requested_scopes)
        resource_url = self.validate_resource(resource)
        existing = await self._repository.get_grant_for_user_client(user_id, client_id)
        already = existing.scopes if existing is not None and existing.is_active() else ()
        return AuthorizationRequestView(
            client_id=client_id,
            client_name=client.client_name or client_id,
            redirect_uri=redirect_uri,
            resource=resource_url,
            requested_scopes=scopes,
            already_granted=tuple(s for s in already if s in scopes),
        )

    async def approve(
        self,
        *,
        user_id: int,
        client_id: str,
        redirect_uri: str,
        requested_scopes: Iterable[str] | None,
        code_challenge: str,
        resource: str | None,
        state: str | None = None,
    ) -> str:
        """Record consent and return the redirect the browser must follow.

        The caller must already have authenticated ``user_id``; this method
        never infers identity from the request. Every value is re-validated
        against the client's registration, because the consent page is a
        browser-supplied round trip and must be treated as untrusted input.
        """
        client = await self._require_client(client_id)
        self._ensure_redirect_uri(client, redirect_uri)
        scopes = self.resolve_scopes(client, requested_scopes)
        resource_url = self.validate_resource(resource)
        if not code_challenge:
            raise InvalidGrantError("A PKCE code_challenge is required.")

        now = self._clock()
        grant = await self._repository.upsert_grant(
            AgentGrant(
                id=str(uuid.uuid4()),
                user_id=user_id,
                client_id=client_id,
                client_name=client.client_name or client_id,
                scopes=scopes,
                status=AgentGrantStatus.ACTIVE,
                resource=resource_url,
                created_at=now,
            )
        )

        code = secrets.token_urlsafe(32)
        await self._repository.save_code(
            AuthorizationCodeRecord(
                code_hash=hash_secret(code),
                grant_id=grant.id,
                client_id=client_id,
                subject=str(user_id),
                scopes=scopes,
                code_challenge=code_challenge,
                redirect_uri=redirect_uri,
                resource=resource_url,
                expires_at=now + self._code_ttl,
            )
        )
        return build_redirect_uri(redirect_uri, code=code, state=state)

    # ------------------------------------------------------------------
    # Token exchange
    # ------------------------------------------------------------------

    async def peek_authorization_code(self, code: str) -> AuthorizationCodeRecord | None:
        """Read a code without consuming it (used by the token endpoint's checks)."""
        return await self._repository.peek_code(hash_secret(code))

    async def peek_refresh_token(self, token: str) -> TokenRecord | None:
        """Read a refresh token without rotating it."""
        return await self._repository.get_token(hash_secret(token))

    async def exchange_code(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str | None,
        resource: str | None = None,
    ) -> IssuedTokens:
        """Redeem a single-use authorization code for tokens.

        PKCE verification itself is performed by the MCP SDK's token endpoint
        (it compares the presented ``code_verifier`` against the stored S256
        challenge) before this is reached; what happens here is the part the
        SDK cannot know about — that the user may have revoked the grant, or
        narrowed its scopes, between consent and redemption.
        """
        now = self._clock()
        record = await self._repository.take_code(hash_secret(code), now)
        if record is None:
            raise InvalidGrantError("The authorization code is invalid, expired or already used.")
        if record.client_id != client_id:
            raise InvalidGrantError("The authorization code was issued to a different client.")
        if redirect_uri is not None and record.redirect_uri != redirect_uri:
            raise InvalidGrantError("The redirect_uri does not match the authorization request.")

        presented_resource = self.validate_resource(resource)
        if record.resource and record.resource != presented_resource:
            raise InvalidTargetError(resource)

        grant = await self._require_active_grant(record.grant_id, record.subject)
        # A re-consent that narrowed the scopes must not be widened back by a
        # code minted before it.
        scopes = tuple(scope for scope in record.scopes if scope in set(grant.scopes))
        if not scopes:
            raise GrantRevokedError()
        return await self._issue(
            grant=grant, scopes=scopes, resource=presented_resource, now=now, with_refresh=True
        )

    async def exchange_refresh(
        self,
        *,
        client_id: str,
        refresh_token: str,
        scopes: Iterable[str] | None = None,
        resource: str | None = None,
    ) -> IssuedTokens:
        """Redeem a refresh token, rotating it (the presented one is revoked)."""
        now = self._clock()
        record = await self._repository.get_token(hash_secret(refresh_token))
        if record is None or record.kind != REFRESH_TOKEN_KIND:
            raise InvalidGrantError("The refresh token is invalid.")
        if record.revoked_at is not None or record.expires_at <= now:
            raise InvalidGrantError("The refresh token is expired or revoked.")
        if record.client_id != client_id:
            raise InvalidGrantError("The refresh token was issued to a different client.")

        presented_resource = self.validate_resource(resource)
        if record.resource and record.resource != presented_resource:
            raise InvalidTargetError(resource)

        grant = await self._require_active_grant(record.grant_id, record.subject)
        narrowed = record.scopes
        if scopes:
            requested = normalize_scopes(scopes)
            if not requested or not is_subset(requested, record.scopes):
                raise InvalidScopeError(list(scopes))
            narrowed = requested

        # Rotation: the presented refresh token dies with the exchange, so a
        # stolen copy is single-use and its replay is a detectable change.
        await self._repository.revoke_token(record.token_hash, now)
        return await self._issue(
            grant=grant, scopes=narrowed, resource=presented_resource, now=now, with_refresh=True
        )

    async def load_access_token(self, token: str) -> TokenRecord | None:
        """Resolve an access token to its record, or ``None`` if unusable.

        Returns ``None`` (rather than raising) because this is the hot path the
        SDK calls on every MCP request: an unusable token is simply "not
        authenticated". Crucially this re-checks the *grant*, so revoking an
        application stops its agent on the next call instead of at expiry.
        """
        now = self._clock()
        record = await self._repository.get_token(hash_secret(token))
        if record is None or record.kind != ACCESS_TOKEN_KIND:
            return None
        if record.revoked_at is not None or record.expires_at <= now:
            return None
        if record.resource is not None and record.resource != self._resource:
            return None
        grant = await self._repository.get_grant(record.grant_id)
        if grant is None or not grant.is_active():
            return None
        if not is_subset(record.scopes, grant.scopes):
            # The grant was narrowed (or re-consented with fewer scopes) after
            # this token was minted: the token is now more powerful than the
            # consent behind it, so it is refused rather than downgraded.
            return None
        await self._repository.touch_grant(record.grant_id, now)
        return record

    async def revoke_token(self, token: str) -> None:
        """Revoke an access or refresh token (RFC 7009).

        Silently ignores an unknown value, as the spec requires — a revocation
        endpoint must not confirm whether a guessed token exists.
        """
        await self._repository.revoke_token(hash_secret(token), self._clock())

    # ------------------------------------------------------------------
    # User-facing management
    # ------------------------------------------------------------------

    async def list_connections(self, user_id: int) -> list[AgentGrant]:
        """Every grant (active or revoked) belonging to ``user_id``."""
        return await self._repository.list_grants(user_id)

    async def revoke_connection(self, user_id: int, grant_id: str) -> AgentGrant | None:
        """Withdraw an application's access. Returns ``None`` if not owned.

        Scoped by ``user_id`` in the repository, so a guessed ``grant_id``
        belonging to somebody else is indistinguishable from one that does not
        exist — there is no ownership oracle to probe.
        """
        now = self._clock()
        grant = await self._repository.revoke_grant(grant_id, user_id)
        if grant is None:
            return None
        # Kill the credentials too, so nothing that was already issued can be
        # revived by flipping the grant back on.
        await self._repository.revoke_tokens_for_grant(grant.id, now)
        return grant

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def resolve_scopes(
        self, client: OAuthClientRecord, requested: Iterable[str] | None
    ) -> tuple[AgentScope, ...]:
        """Decide the scope set for this authorization request.

        Three refusals, each closing a different hole:

        * a value that is not a scope we know (fail closed — never silently
          dropped, because dropping is how "grant read" becomes "grant
          everything the default happens to be");
        * a scope outside the deployment's ``grantable_scopes`` (an operator can
          disable destructive access platform-wide);
        * a scope the *client* did not register for.

        An empty request yields :data:`DEFAULT_SCOPES` — read, convert and
        write, never delete.
        """
        client_scope_values = {
            value
            for value in (client.scope or "").split()
            if value
        }
        raw = [value.strip() for value in (requested or ()) if value and value.strip()]
        unknown = [value for value in raw if value not in _KNOWN_SCOPE_VALUES]
        if unknown:
            raise InvalidScopeError(unknown)
        scopes = normalize_scopes(raw) if raw else DEFAULT_SCOPES
        disallowed = [scope.value for scope in scopes if scope not in self._grantable]
        if disallowed:
            raise InvalidScopeError(disallowed)
        if client_scope_values:
            outside = [scope.value for scope in scopes if scope.value not in client_scope_values]
            if outside:
                raise InvalidScopeError(outside)
        return scopes

    def validate_resource(self, resource: str | None) -> str:
        """Bind the request to this server's RFC 8707 resource identifier.

        An omitted ``resource`` is accepted and bound to ours (clients that
        predate the requirement keep working); a *different* one is refused.
        Accepting a foreign resource would be exactly the token-passthrough
        confusion the MCP specification prohibits.
        """
        if not resource or not resource.strip():
            return self._resource
        candidate = resource.strip().rstrip("/")
        if candidate != self._resource:
            raise InvalidTargetError(resource)
        return self._resource

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _require_client(self, client_id: str) -> OAuthClientRecord:
        client = await self._repository.get_client(client_id)
        if client is None:
            raise UnknownClientError(client_id)
        return client

    @staticmethod
    def _ensure_redirect_uri(client: OAuthClientRecord, redirect_uri: str) -> None:
        """Exact-match the redirect URI against the registered set."""
        registered = {str(uri) for uri in client.redirect_uris}
        if redirect_uri not in registered:
            raise InvalidRedirectUriError()

    async def _require_active_grant(self, grant_id: str, subject: str) -> AgentGrant:
        grant = await self._repository.get_grant(grant_id)
        if grant is None or str(grant.user_id) != str(subject):
            # Same error for "no such grant" and "someone else's grant": the
            # caller must not be able to distinguish the two.
            raise InvalidGrantError()
        if not grant.is_active():
            raise GrantRevokedError()
        return grant

    async def _issue(
        self,
        *,
        grant: AgentGrant,
        scopes: tuple[AgentScope, ...],
        resource: str,
        now: datetime,
        with_refresh: bool,
    ) -> IssuedTokens:
        access_token = secrets.token_urlsafe(32)
        await self._repository.save_token(
            TokenRecord(
                token_hash=hash_secret(access_token),
                grant_id=grant.id,
                client_id=grant.client_id,
                subject=str(grant.user_id),
                kind=ACCESS_TOKEN_KIND,
                scopes=scopes,
                resource=resource,
                expires_at=now + self._access_ttl,
            )
        )
        refresh_token = None
        if with_refresh:
            refresh_token = secrets.token_urlsafe(32)
            await self._repository.save_token(
                TokenRecord(
                    token_hash=hash_secret(refresh_token),
                    grant_id=grant.id,
                    client_id=grant.client_id,
                    subject=str(grant.user_id),
                    kind=REFRESH_TOKEN_KIND,
                    scopes=scopes,
                    resource=resource,
                    expires_at=now + self._refresh_ttl,
                )
            )
        return IssuedTokens(
            access_token=access_token,
            expires_in=int(self._access_ttl.total_seconds()),
            scopes=scopes,
            refresh_token=refresh_token,
        )
