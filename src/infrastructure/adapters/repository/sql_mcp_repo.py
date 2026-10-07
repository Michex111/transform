"""SQLAlchemy repository for MCP clients, grants, codes and tokens.

Two implementation notes that matter for correctness rather than style:

* **Redeeming a code is a compare-and-swap.** ``take_code`` performs a single
  conditional ``UPDATE`` (``used_at`` becomes non-null only where it was null
  and the code has not expired) and treats ``rowcount == 0`` as "not
  redeemable". Read-then-write would let two concurrent requests both observe
  an unused code and both be issued tokens.
* **``touch_grant`` is conditional, not unconditional.** It runs on every
  authenticated MCP request, so it only writes when the stored ``last_used_at``
  is missing or older than a refresh window — the same reasoning (and window) as
  ``SQLAPIKeyRepository.touch_last_used``. An unconditional UPDATE would put a
  write on the hot path of every tool call.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.ports.mcp_oauth_port import (
    AuthorizationCodeRecord,
    OAuthClientRecord,
    TokenRecord,
)
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.value_object.agent_scope import normalize_scopes
from src.infrastructure.database.models import (
    MCPAgentGrantModel,
    MCPAuthorizationCodeModel,
    MCPOAuthClientModel,
    MCPTokenModel,
)

#: ``last_used_at`` is a coarse "was this app used recently?" signal, not an
#: audit-exact timestamp. Refreshing it at most every 5 minutes keeps the
#: meaning while avoiding a write + commit on every single tool call.
_LAST_USED_REFRESH_INTERVAL = timedelta(minutes=5)


def _aware(value: datetime | None) -> datetime | None:
    """Normalise a driver-returned datetime to an aware UTC one.

    SQLite (and some other drivers) hand back **naive** datetimes for a
    ``DateTime(timezone=True)`` column, and the first comparison against
    ``datetime.now(UTC)`` then raises ``TypeError: can't compare offset-naive
    and offset-aware datetimes``. Normalising here — at the boundary where the
    driver's behaviour leaks in — is what keeps the application layer able to
    compare these values without knowing which database is behind it. Same fix,
    same reason, as ``domain/security/enitities/api_key.py::is_valid``.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SQLMCPRepository:
    """Persists the MCP authorization server's state."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    async def save_client(self, client: OAuthClientRecord) -> None:
        existing = await self._session.get(MCPOAuthClientModel, client.client_id)
        values = {
            "client_secret": client.client_secret,
            "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris),
            "grant_types": list(client.grant_types),
            "response_types": list(client.response_types),
            "scope": client.scope,
            "token_endpoint_auth_method": client.token_endpoint_auth_method,
        }
        if existing is None:
            self._session.add(
                MCPOAuthClientModel(
                    client_id=client.client_id,
                    created_at=datetime.now(UTC),
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        await self._session.commit()

    async def get_client(self, client_id: str) -> OAuthClientRecord | None:
        row = await self._session.get(MCPOAuthClientModel, client_id)
        if row is None:
            return None
        return OAuthClientRecord(
            client_id=row.client_id,
            client_secret=row.client_secret,
            client_name=row.client_name,
            redirect_uris=tuple(row.redirect_uris or ()),
            grant_types=tuple(row.grant_types or ()),
            response_types=tuple(row.response_types or ()),
            scope=row.scope,
            token_endpoint_auth_method=row.token_endpoint_auth_method,
        )

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------

    async def upsert_grant(self, grant: AgentGrant) -> AgentGrant:
        """Insert the grant, or reactivate the existing (user, client) one.

        Re-consent replaces the scope set outright rather than merging it: a
        user who unticks "delete" must actually lose it, and an older token
        whose scopes are no longer covered is refused by
        ``MCPAccessService.load_access_token``.
        """
        result = await self._session.execute(
            select(MCPAgentGrantModel).where(
                MCPAgentGrantModel.user_id == grant.user_id,
                MCPAgentGrantModel.client_id == grant.client_id,
            )
        )
        row = result.scalars().first()
        scope_string = " ".join(scope.value for scope in grant.scopes)
        if row is None:
            self._session.add(
                MCPAgentGrantModel(
                    id=grant.id,
                    user_id=grant.user_id,
                    client_id=grant.client_id,
                    client_name=grant.client_name,
                    scopes=scope_string,
                    status=AgentGrantStatus.ACTIVE.value,
                    resource=grant.resource,
                    created_at=grant.created_at or datetime.now(UTC),
                )
            )
        else:
            row.client_name = grant.client_name
            row.scopes = scope_string
            row.status = AgentGrantStatus.ACTIVE.value
            row.resource = grant.resource
            # A fresh consent clears the previous revocation, because the user
            # has just explicitly re-approved the application.
            row.revoked_at = None
        await self._session.commit()
        stored = await self.get_grant_for_user_client(grant.user_id, grant.client_id)
        assert stored is not None  # just written
        return stored

    async def get_grant(self, grant_id: str) -> AgentGrant | None:
        row = await self._session.get(MCPAgentGrantModel, grant_id)
        return self._to_grant(row)

    async def get_grant_for_user_client(self, user_id: int, client_id: str) -> AgentGrant | None:
        result = await self._session.execute(
            select(MCPAgentGrantModel).where(
                MCPAgentGrantModel.user_id == user_id,
                MCPAgentGrantModel.client_id == client_id,
            )
        )
        return self._to_grant(result.scalars().first())

    async def list_grants(self, user_id: int) -> list[AgentGrant]:
        result = await self._session.execute(
            select(MCPAgentGrantModel)
            .where(MCPAgentGrantModel.user_id == user_id)
            .order_by(MCPAgentGrantModel.created_at.desc())
        )
        grants = [self._to_grant(row) for row in result.scalars().all()]
        return [grant for grant in grants if grant is not None]

    async def revoke_grant(self, grant_id: str, user_id: int) -> AgentGrant | None:
        """Revoke a grant the caller owns. Foreign/missing both return ``None``."""
        result = await self._session.execute(
            select(MCPAgentGrantModel).where(
                MCPAgentGrantModel.id == grant_id,
                MCPAgentGrantModel.user_id == user_id,
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        if row.status != AgentGrantStatus.REVOKED.value:
            row.status = AgentGrantStatus.REVOKED.value
            row.revoked_at = datetime.now(UTC)
            await self._session.commit()
        return self._to_grant(row)

    async def touch_grant(self, grant_id: str, used_at: datetime) -> None:
        """Record last use, at most once per refresh window."""
        await self._session.execute(
            update(MCPAgentGrantModel)
            .where(
                MCPAgentGrantModel.id == grant_id,
                or_(
                    MCPAgentGrantModel.last_used_at.is_(None),
                    MCPAgentGrantModel.last_used_at
                    < used_at - _LAST_USED_REFRESH_INTERVAL,
                ),
            )
            .values(last_used_at=used_at)
        )
        await self._session.commit()

    # ------------------------------------------------------------------
    # Authorization codes
    # ------------------------------------------------------------------

    async def save_code(self, code: AuthorizationCodeRecord) -> None:
        self._session.add(
            MCPAuthorizationCodeModel(
                code_hash=code.code_hash,
                grant_id=code.grant_id,
                client_id=code.client_id,
                subject=code.subject,
                scopes=" ".join(scope.value for scope in code.scopes),
                code_challenge=code.code_challenge,
                redirect_uri=code.redirect_uri,
                resource=code.resource,
                expires_at=code.expires_at,
                used_at=code.used_at,
            )
        )
        await self._session.commit()

    async def peek_code(self, code_hash: str) -> AuthorizationCodeRecord | None:
        """Read a code without consuming it."""
        row = await self._session.get(MCPAuthorizationCodeModel, code_hash)
        return self._to_code(row)

    async def take_code(self, code_hash: str, now: datetime) -> AuthorizationCodeRecord | None:
        """Atomically mark an unexpired, unused code used and return it."""
        result = await self._session.execute(
            update(MCPAuthorizationCodeModel)
            .where(
                MCPAuthorizationCodeModel.code_hash == code_hash,
                MCPAuthorizationCodeModel.used_at.is_(None),
                MCPAuthorizationCodeModel.expires_at > now,
            )
            .values(used_at=now)
        )
        await self._session.commit()
        if not result.rowcount:  # type: ignore[attr-defined]
            return None
        row = await self._session.get(MCPAuthorizationCodeModel, code_hash)
        return self._to_code(row)

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    async def save_token(self, token: TokenRecord) -> None:
        self._session.add(
            MCPTokenModel(
                token_hash=token.token_hash,
                grant_id=token.grant_id,
                client_id=token.client_id,
                subject=token.subject,
                kind=token.kind,
                scopes=" ".join(scope.value for scope in token.scopes),
                resource=token.resource,
                expires_at=token.expires_at,
                created_at=datetime.now(UTC),
            )
        )
        await self._session.commit()

    async def get_token(self, token_hash: str) -> TokenRecord | None:
        row = await self._session.get(MCPTokenModel, token_hash)
        if row is None:
            return None
        return TokenRecord(
            token_hash=row.token_hash,
            grant_id=row.grant_id,
            client_id=row.client_id,
            subject=row.subject,
            kind=row.kind,
            scopes=normalize_scopes(row.scopes.split()),
            resource=row.resource,
            expires_at=_aware(row.expires_at),  # type: ignore[arg-type]
            revoked_at=_aware(row.revoked_at),
        )

    async def revoke_token(self, token_hash: str, revoked_at: datetime) -> None:
        await self._session.execute(
            update(MCPTokenModel)
            .where(MCPTokenModel.token_hash == token_hash, MCPTokenModel.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        await self._session.commit()

    async def revoke_tokens_for_grant(self, grant_id: str, revoked_at: datetime) -> int:
        result = await self._session.execute(
            update(MCPTokenModel)
            .where(MCPTokenModel.grant_id == grant_id, MCPTokenModel.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        await self._session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_expired(self, before: datetime) -> int:
        """Purge spent/expired codes and expired tokens."""
        codes = await self._session.execute(
            delete(MCPAuthorizationCodeModel).where(
                MCPAuthorizationCodeModel.expires_at < before
            )
        )
        tokens = await self._session.execute(
            delete(MCPTokenModel).where(MCPTokenModel.expires_at < before)
        )
        await self._session.commit()
        return int((codes.rowcount or 0) + (tokens.rowcount or 0))  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _to_code(row: MCPAuthorizationCodeModel | None) -> AuthorizationCodeRecord | None:
        if row is None:
            return None
        return AuthorizationCodeRecord(
            code_hash=row.code_hash,
            grant_id=row.grant_id,
            client_id=row.client_id,
            subject=row.subject,
            scopes=normalize_scopes(row.scopes.split()),
            code_challenge=row.code_challenge,
            redirect_uri=row.redirect_uri,
            resource=row.resource,
            expires_at=_aware(row.expires_at),  # type: ignore[arg-type]
            used_at=_aware(row.used_at),
        )

    @staticmethod
    def _to_grant(row: MCPAgentGrantModel | None) -> AgentGrant | None:
        if row is None:
            return None
        try:
            status = AgentGrantStatus(row.status)
        except ValueError:
            # An unrecognised status can only come from manual database surgery;
            # treating it as revoked is the fail-closed reading.
            status = AgentGrantStatus.REVOKED
        return AgentGrant(
            id=row.id,
            user_id=row.user_id,
            client_id=row.client_id,
            client_name=row.client_name,
            scopes=normalize_scopes(row.scopes.split()),
            status=status,
            resource=row.resource,
            created_at=_aware(row.created_at),
            last_used_at=_aware(row.last_used_at),
            revoked_at=_aware(row.revoked_at),
        )


def hash_token(value: str) -> str:
    """SHA-256 hex digest, matching ``MCPAccessService.hash_secret``.

    Re-exported here only so an operator tool/script can compute the same hash
    without importing the application layer.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
