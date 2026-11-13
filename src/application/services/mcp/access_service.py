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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.application.exceptions.mcp_exceptions import (
    ConnectionEditError,
    ConnectionStateError,
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
from src.domain.security.exceptions.exceptions import InvalidGrantTransition
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    KNOWN_REQUEST_SCOPES,
    AgentScope,
    normalize_scopes,
)
from src.infrastructure.logging.audit import log_connection_control

#: Token discriminators. Plain strings so the application layer stays free of
#: the ORM's enum while the repository can persist them directly.
ACCESS_TOKEN_KIND = "ACCESS"
REFRESH_TOKEN_KIND = "REFRESH"

#: How stale ``last_used_at`` must be before an authenticated MCP call writes it.
#:
#: Mirrors ``_LAST_USED_REFRESH_INTERVAL`` in the SQL repository, which applies the
#: same window in its conditional UPDATE. Checking it here as well turns the
#: common case into **no write at all** (and therefore one fewer round trip on
#: every tool call); the SQL condition remains the authority, so a clock skew
#: between the two can only cause a write that the UPDATE would have made safe.
_GRANT_TOUCH_INTERVAL = timedelta(minutes=5)

#: Scope strings this server recognises in a REQUEST. Broader than
#: ``ALL_SCOPES`` by the request markers (``offline_access``), which are accepted
#: and then dropped by ``normalize_scopes`` — so they can never be persisted as a
#: capability. Validating against ``ALL_SCOPES`` instead is what made every
#: request that asked for ``offline_access`` fail outright.
_KNOWN_SCOPE_VALUES = frozenset(KNOWN_REQUEST_SCOPES)


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
    #: Every scope the user *may* choose for this connection: the client's
    #: registered set intersected with the deployment's grantable set.
    #:
    #: Distinct from :attr:`requested_scopes` on purpose. An authorization
    #: request that names no scope resolves to ``DEFAULT_SCOPES`` (read, convert,
    #: write), but the user must still be able to turn on workspaces or delete
    #: when they choose to — otherwise those capabilities cannot be granted at
    #: all from any client that omits ``scope`` (which is most of them).
    #: ``requested_scopes`` is what the screen pre-checks; ``available_scopes``
    #: is what it shows.
    available_scopes: tuple[AgentScope, ...]
    #: Scopes this user already granted this client, so the screen can say
    #: "already allowed" instead of pretending the decision is new.
    already_granted: tuple[AgentScope, ...]
    #: The binding the user already has, so re-consenting shows the existing
    #: choice rather than silently resetting it. Defaults to whole-Drive /
    #: agent-only when there is no grant to read.
    folder_access: FolderAccess = FolderAccess.ALL
    folder_id: str | None = None
    history_scope: HistoryScope = HistoryScope.AGENT


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
        refresh_reuse_grace_seconds: int = 60,
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
        self._refresh_grace = timedelta(seconds=max(0, refresh_reuse_grace_seconds))
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
        available = self.available_scopes(client)
        resource_url = self.validate_resource(resource)
        existing = await self._repository.get_grant_for_user_client(user_id, client_id)
        already = existing.scopes if existing is not None and existing.is_active() else ()
        return AuthorizationRequestView(
            client_id=client_id,
            client_name=client.client_name or client_id,
            redirect_uri=redirect_uri,
            resource=resource_url,
            requested_scopes=scopes,
            available_scopes=available,
            already_granted=tuple(s for s in already if s in available),
            # Report the binding the user already has so a re-consent screen
            # reflects it instead of defaulting to whole-Drive, which would look
            # like a fresh decision. With no grant, the least-access defaults win.
            folder_access=existing.folder_access if existing is not None else FolderAccess.ALL,
            folder_id=existing.folder_id if existing is not None else None,
            history_scope=(
                existing.history_scope if existing is not None else HistoryScope.AGENT
            ),
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
        folder_access: FolderAccess = FolderAccess.ALL,
        folder_id: str | None = None,
        history_scope: HistoryScope = HistoryScope.AGENT,
        approved_scopes: Iterable[str] | None = None,
    ) -> str:
        """Record consent and return the redirect the browser must follow.

        The caller must already have authenticated ``user_id``; this method
        never infers identity from the request. Every value is re-validated
        against the client's registration, because the consent page is a
        browser-supplied round trip and must be treated as untrusted input.

        The folder binding (``folder_access``/``folder_id``) and
        ``history_scope`` are stored **verbatim**: resolving a folder name or
        proving the folder belongs to the user requires file-service I/O, which
        deliberately lives outside this layer. The router resolves and
        ownership-checks the binding before calling this, and the toolbox denies
        a restricted grant whose folder cannot be resolved.
        """
        client = await self._require_client(client_id)
        self._ensure_redirect_uri(client, redirect_uri)
        if approved_scopes is None:
            # No explicit decision: grant exactly what was requested (the
            # pre-existing behaviour).
            scopes = self.resolve_scopes(client, requested_scopes)
        else:
            # The user's decision, checked against what this client *may* be
            # granted rather than only against what it asked for. That is what
            # makes a capability the request never named (workspaces.*, delete)
            # grantable at all, while still refusing anything the deployment or
            # the client's registration does not allow.
            available = set(self.available_scopes(client))
            scopes = normalize_scopes(approved_scopes)
            outside = [scope.value for scope in scopes if scope not in available]
            if outside:
                raise InvalidScopeError(outside, reason="client")
            if not scopes:
                raise InvalidGrantError("Select at least one permission to allow.")
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
                folder_access=folder_access,
                folder_id=folder_id,
                history_scope=history_scope,
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
        """Redeem a refresh token, rotating it atomically.

        Two properties this method exists to guarantee, both of which were
        previously bugs that disconnected real clients:

        * **The grant is the authority.** The issued scope set is the *grant's
          current* scopes (optionally narrowed by the request), never the scopes
          frozen into the token at issue time. A user who turns a permission on
          or off in Settings therefore takes effect on the next refresh instead
          of requiring a fresh consent, and a narrowing never leaves the client
          holding a token that the verifier then refuses.
        * **Rotation is atomic and a retry is not a replay.** The presented token
          is revoked by a single conditional UPDATE; if that loses a race, or the
          token was rotated moments ago, a request inside
          ``MCP_REFRESH_REUSE_GRACE_SECONDS`` receives a fresh pair instead of
          ``invalid_grant``. The successor minted by the request being retried is
          revoked, so exactly one refresh token stays live. After the window a
          replay is refused exactly as before.
        """
        now = self._clock()
        record = await self._repository.get_token(hash_secret(refresh_token))
        if record is None or record.kind != REFRESH_TOKEN_KIND:
            raise InvalidGrantError("The refresh token is invalid.")
        if record.expires_at <= now:
            raise InvalidGrantError("The refresh token is expired or revoked.")
        if record.client_id != client_id:
            raise InvalidGrantError("The refresh token was issued to a different client.")

        presented_resource = self.validate_resource(resource)
        if record.resource and record.resource != presented_resource:
            raise InvalidTargetError(resource)

        grant = await self._require_active_grant(record.grant_id, record.subject)

        # A retry arrives with a token that is already revoked. Only a *rotated*
        # one, inside the grace window, and for the same client is acceptable.
        if record.revoked_at is not None:
            if not self._within_reuse_grace(record, now):
                raise InvalidGrantError("The refresh token is expired or revoked.")
            # Mint a fresh pair and retire the successor the lost request
            # created, so the connection never accumulates live refresh tokens.
            successor = record.replaced_by
            issued = await self._issue(
                grant=grant,
                scopes=self._effective_scopes(grant, scopes),
                resource=presented_resource,
                now=now,
                with_refresh=True,
            )
            if successor:
                await self._repository.revoke_successor(successor, now)
            return issued

        # Reserve the successor before the atomic rotate: the repository stores
        # only hashes, so it must learn the successor's hash at rotation time.
        successor_plaintext = secrets.token_urlsafe(32)
        rotated = await self._repository.rotate_refresh_token(
            record.token_hash, now, hash_secret(successor_plaintext)
        )
        if rotated is None:
            # Another request rotated it between our read and our UPDATE. Treat
            # it exactly like the retry path above: inside the window the client
            # gets working tokens; outside it, a refusal.
            latest = await self._repository.get_token(record.token_hash)
            if latest is None or not self._within_reuse_grace(latest, now):
                raise InvalidGrantError("The refresh token is expired or revoked.")
            issued = await self._issue(
                grant=grant,
                scopes=self._effective_scopes(grant, scopes),
                resource=presented_resource,
                now=now,
                with_refresh=True,
            )
            if latest.replaced_by:
                await self._repository.revoke_successor(latest.replaced_by, now)
            return issued

        return await self._issue(
            grant=grant,
            scopes=self._effective_scopes(grant, scopes),
            resource=presented_resource,
            now=now,
            with_refresh=True,
            refresh_plaintext=successor_plaintext,
        )

    def _within_reuse_grace(self, record: TokenRecord, now: datetime) -> bool:
        """Whether a revoked refresh token may still be exchanged as a retry.

        Requires an actual rotation (``replaced_by`` set) — a token revoked by
        the user, or by :meth:`revoke_connection`, has no successor and is
        always refused — and a ``revoked_at`` inside the configured window.
        """
        if (
            record.replaced_by is None
            or record.revoked_at is None
            or self._refresh_grace <= timedelta(0)
        ):
            return False
        revoked_at = record.revoked_at
        if revoked_at.tzinfo is None:
            revoked_at = revoked_at.replace(tzinfo=UTC)
        return now - revoked_at <= self._refresh_grace

    @staticmethod
    def _effective_scopes(
        grant: AgentGrant, requested: Iterable[str] | None
    ) -> tuple[AgentScope, ...]:
        """The scopes a refreshed token should carry: the grant's, narrowed.

        Never the token's frozen set and never anything the grant lacks, so a
        refresh can neither resurrect a revoked capability nor silently drop one
        the user has since granted.
        """
        current = tuple(grant.scopes)
        if not requested:
            return current
        wanted = normalize_scopes(requested)
        narrowed = tuple(scope for scope in current if scope in set(wanted))
        if not narrowed:
            raise InvalidScopeError(list(requested))
        return narrowed

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
        # The grant is the authority, not the scopes frozen into the token.
        #
        # This used to reject any token whose scopes were not a subset of the
        # grant's, which meant that *narrowing* a connection in Settings left
        # every refreshed token immediately unusable (the client looped
        # refresh → 401 → refresh until it gave up), and it also meant that
        # *widening* one had no effect until a full re-authorization. Both are
        # fixed by resolving the effective set here, on every request: the token
        # is a handle, the grant is the permission. The token's own scopes are
        # deliberately not an upper bound — a grant that gains a capability is
        # meant to be usable at once, which is what the Connected-apps screen
        # promises.
        effective = replace(record, scopes=tuple(grant.scopes))
        last_used = grant.last_used_at
        if last_used is not None and last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=UTC)
        if last_used is None or now - last_used >= _GRANT_TOUCH_INTERVAL:
            # Only write when the signal is actually stale. The repository's
            # UPDATE would be a no-op otherwise, but it would still cost a round
            # trip on the hot path of every tool call.
            await self._repository.touch_grant(record.grant_id, now)
        return effective

    async def effective_scopes_for_grant(self, grant_id: str) -> tuple[AgentScope, ...]:
        """The scopes a token for ``grant_id`` should be treated as holding.

        One place for the "grant is the authority" rule, so the tool transport
        and the download capability cannot disagree about it.
        """
        grant = await self._repository.get_grant(grant_id)
        if grant is None or not grant.is_active():
            return ()
        return tuple(grant.scopes)

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
            log_connection_control(
                "revoke", str(user_id), grant_id, success=False, reason="not_found"
            )
            return None
        # Kill the credentials too, so nothing that was already issued can be
        # revived by flipping the grant back on.
        await self._repository.revoke_tokens_for_grant(grant.id, now)
        log_connection_control(
            "revoke",
            str(user_id),
            grant.id,
            success=True,
            client_id=grant.client_id,
        )
        return grant

    async def pause_connection(self, user_id: int, grant_id: str) -> AgentGrant | None:
        """Suspend an application's access without revoking it.

        Takes effect on the next MCP request, not at token expiry: access tokens
        are opaque and resolved through the grant row, and
        :meth:`load_access_token` refuses a grant whose :meth:`is_active` is
        false — which a paused grant is. An already-open MCP session therefore
        cannot keep working by virtue of holding a live token.

        In-flight tool calls are **not** cancelled. A call already executing
        reads its data through the toolbox at the moment it runs; there is no
        cooperative cancellation point to interrupt it safely, and claiming
        otherwise would be a false promise. What is guaranteed is that no
        *subsequent* call succeeds. Returns ``None`` if the grant is not the
        caller's.
        """
        return await self._set_status(user_id, grant_id, AgentGrantStatus.PAUSED)

    async def resume_connection(self, user_id: int, grant_id: str) -> AgentGrant | None:
        """Undo a pause, restoring exactly the scopes already granted.

        Refuses a revoked grant: revocation is terminal and regaining access
        requires a fresh authorization flow. Never widens access — the stored
        scope set is untouched.
        """
        return await self._set_status(user_id, grant_id, AgentGrantStatus.ACTIVE)

    async def _set_status(
        self, user_id: int, grant_id: str, status: AgentGrantStatus
    ) -> AgentGrant | None:
        action = "pause" if status is AgentGrantStatus.PAUSED else "resume"
        try:
            grant = await self._repository.set_grant_status(
                grant_id, user_id, status, self._clock()
            )
        except InvalidGrantTransition as exc:
            log_connection_control(
                action, str(user_id), grant_id, success=False, reason="revoked"
            )
            raise ConnectionStateError(str(exc)) from exc
        if grant is None:
            log_connection_control(
                action, str(user_id), grant_id, success=False, reason="not_found"
            )
            return None
        log_connection_control(
            action,
            str(user_id),
            grant.id,
            success=True,
            client_id=grant.client_id,
        )
        return grant

    async def update_connected_app(
        self,
        *,
        user_id: int,
        grant_id: str,
        scopes: Iterable[str],
        folder_access: FolderAccess,
        folder_id: str | None,
        history_scope: HistoryScope,
        confirm_destructive: bool = False,
    ) -> AgentGrant | None:
        """Edit an existing connection's permissions in place.

        Unlike :meth:`approve`, this is **not** a re-consent and therefore never
        moves the grant's lifecycle: ``status``, ``paused_at`` and
        ``revoked_at`` are left exactly as they were. A paused connection stays
        paused. That is the whole reason this delegates to
        :meth:`MCPRepositoryPort.update_grant_binding` rather than reusing
        :meth:`~MCPRepositoryPort.upsert_grant`, which reactivates the row.

        Returns ``None`` when the grant does not exist or belongs to somebody
        else, so the caller cannot use this to probe for another account's ids —
        the same contract as :meth:`revoke_connection`. A **revoked** grant is
        refused with :class:`ConnectionStateError`: revocation is terminal and
        regaining access requires a fresh consent.

        Scope handling fails closed, twice over:

        * an unrecognised scope is **refused**, not dropped — ``normalize_scopes``
          silently discards unknown values, so that drop is detected here and
          turned into an error rather than persisted as a silently-narrowed set;
        * an empty result is refused outright: a connection with no permissions
          is a revocation, which is what the revoke endpoint is for, and storing
          one would leave a confusing "connected but useless" row.

        Adding ``documents.delete`` to a grant that did not already hold it
        requires ``confirm_destructive=True``, because it is irreversible.
        Removing it never does.
        """
        grant = await self._repository.get_grant(grant_id)
        if grant is None or grant.user_id != user_id:
            # Not-found and not-owned are the same answer, on purpose: a
            # distinct 403 would confirm that somebody else's grant id exists.
            return None
        if grant.status is AgentGrantStatus.REVOKED:
            raise ConnectionStateError(
                "A revoked connection cannot be edited. "
                "Reconnect the application to grant access again."
            )

        requested = [value.strip() for value in scopes]
        unknown = [
            value for value in requested if value and value not in _KNOWN_SCOPE_VALUES
        ]
        if unknown:
            raise InvalidScopeError(unknown)
        normalized = normalize_scopes(requested)
        if not normalized:
            raise ConnectionEditError(
                "A connection must keep at least one permission. "
                "To remove access entirely, revoke the connection instead."
            )
        if (
            AgentScope.DOCUMENTS_DELETE in normalized
            and AgentScope.DOCUMENTS_DELETE not in set(grant.scopes)
            and not confirm_destructive
        ):
            raise ConnectionEditError(
                "Allowing the agent to permanently delete your files is irreversible. "
                "Re-send the change with confirm_destructive=true to allow it."
            )

        updated = await self._repository.update_grant_binding(
            grant_id=grant_id,
            user_id=user_id,
            scopes=normalized,
            folder_access=folder_access,
            folder_id=folder_id,
            history_scope=history_scope,
        )
        if updated is None:
            # The row vanished or changed hands between the read above and the
            # write; report it exactly like not-found rather than leaking it.
            return None
        log_connection_control(
            "update_permissions",
            str(user_id),
            updated.id,
            success=True,
            client_id=updated.client_id,
        )
        return updated

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

        The result contains only capabilities. A REQUEST MARKER such as
        ``offline_access`` is accepted here (it is a known scope string, so it is
        not an unknown-scope error) and then dropped by ``normalize_scopes`` —
        which is what stops it being echoed back as something the agent holds or
        rendered as a permission the user could tick.
        """
        client_scope_values = {
            value
            for value in (client.scope or "").split()
            if value
        }
        raw = [value.strip() for value in (requested or ()) if value and value.strip()]
        unknown = [value for value in raw if value not in _KNOWN_SCOPE_VALUES]
        if unknown:
            raise InvalidScopeError(unknown, reason="unknown")
        scopes = normalize_scopes(raw) if raw else DEFAULT_SCOPES
        disallowed = [scope.value for scope in scopes if scope not in self._grantable]
        if disallowed:
            raise InvalidScopeError(disallowed, reason="deployment")
        if client_scope_values:
            outside = [scope.value for scope in scopes if scope.value not in client_scope_values]
            if outside:
                # Reason matters here: this is the one refusal the user can act
                # on, by reconnecting the application so it registers for more.
                raise InvalidScopeError(outside, reason="client")
        return scopes

    def available_scopes(self, client: OAuthClientRecord) -> tuple[AgentScope, ...]:
        """Every scope this client may be granted here, whether asked for or not.

        The consent screen shows these, unchecked unless requested. It is the
        client's registered set (which the SDK fills with the full request
        vocabulary when a client omits ``scope``) intersected with the
        deployment's ``grantable_scopes``.

        This is deliberately broader than :meth:`resolve_scopes`: that decides
        what an authorization *request* means; this decides what a *person* may
        tick. Conflating the two is what made ``workspaces.*`` and
        ``documents.delete`` impossible to grant from any client that omitted
        ``scope`` at registration.
        """
        registered = {value for value in (client.scope or "").split() if value}
        candidates = [
            scope for scope in ALL_SCOPES
            if scope in self._grantable and (not registered or scope.value in registered)
        ]
        return tuple(candidates)

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
        registered = set(client.redirect_uris)
        if redirect_uri not in registered:
            raise InvalidRedirectUriError()

    async def _require_active_grant(self, grant_id: str, subject: str) -> AgentGrant:
        grant = await self._repository.get_grant(grant_id)
        if grant is None or str(grant.user_id) != subject:
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
        refresh_plaintext: str | None = None,
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
            refresh_token = refresh_plaintext or secrets.token_urlsafe(32)
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
