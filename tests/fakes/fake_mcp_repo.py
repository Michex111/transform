"""In-memory ``MCPRepositoryPort`` for unit tests.

Mirrors the SQL adapter's *semantics* rather than just its shape, because the
semantics are what the tests are about:

* ``take_code`` is single-use (the second call returns ``None``);
* ``upsert_grant`` replaces the scope set and clears a previous revocation *and*
  a previous pause (re-consenting is a stronger act than resuming);
* ``revoke_grant`` is scoped by ``user_id`` and returns ``None`` for a foreign
  grant, and is terminal — ``set_grant_status`` refuses to move a revoked grant.
"""

from datetime import UTC, datetime

from src.application.ports.mcp_oauth_port import (
    AuthorizationCodeRecord,
    OAuthClientRecord,
    TokenRecord,
)
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.exceptions.exceptions import InvalidGrantTransition


class FakeMCPRepository:
    """Dict-backed MCP repository."""

    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientRecord] = {}
        self.grants: dict[str, AgentGrant] = {}
        self.codes: dict[str, AuthorizationCodeRecord] = {}
        self.tokens: dict[str, TokenRecord] = {}
        self.touched: list[tuple[str, datetime]] = []

    # -- clients ------------------------------------------------------
    async def save_client(self, client: OAuthClientRecord) -> None:
        self.clients[client.client_id] = client

    async def get_client(self, client_id: str) -> OAuthClientRecord | None:
        return self.clients.get(client_id)

    # -- grants -------------------------------------------------------
    async def upsert_grant(self, grant: AgentGrant) -> AgentGrant:
        for existing in self.grants.values():
            if existing.user_id == grant.user_id and existing.client_id == grant.client_id:
                existing.client_name = grant.client_name
                existing.scopes = grant.scopes
                existing.status = AgentGrantStatus.ACTIVE
                existing.resource = grant.resource
                existing.revoked_at = None
                existing.paused_at = None
                return existing
        self.grants[grant.id] = grant
        return grant

    async def get_grant(self, grant_id: str) -> AgentGrant | None:
        return self.grants.get(grant_id)

    async def get_grant_for_user_client(self, user_id: int, client_id: str) -> AgentGrant | None:
        for grant in self.grants.values():
            if grant.user_id == user_id and grant.client_id == client_id:
                return grant
        return None

    async def list_grants(self, user_id: int) -> list[AgentGrant]:
        return [grant for grant in self.grants.values() if grant.user_id == user_id]

    async def revoke_grant(self, grant_id: str, user_id: int) -> AgentGrant | None:
        grant = self.grants.get(grant_id)
        if grant is None or grant.user_id != user_id:
            return None
        grant.revoke(now=datetime.now(UTC))
        return grant

    async def touch_grant(self, grant_id: str, used_at: datetime) -> None:
        self.touched.append((grant_id, used_at))

    async def set_grant_status(
        self,
        grant_id: str,
        user_id: int,
        status: AgentGrantStatus,
        now: datetime,
    ) -> AgentGrant | None:
        """Pause or resume, scoped to the owner and refusing a revoked grant.

        Delegates the transition to the entity so the fake cannot diverge from
        the rule the SQL adapter enforces — a fake that quietly permits
        something production refuses hides exactly the bug a test should catch.
        """
        grant = self.grants.get(grant_id)
        if grant is None or grant.user_id != user_id:
            return None
        if grant.status is AgentGrantStatus.REVOKED:
            raise InvalidGrantTransition("A revoked grant cannot be paused or resumed.")
        if grant.status is status:
            return grant
        if status is AgentGrantStatus.PAUSED:
            grant.pause(now=now)
        else:
            grant.resume()
        return grant

    # -- codes --------------------------------------------------------
    async def save_code(self, code: AuthorizationCodeRecord) -> None:
        self.codes[code.code_hash] = code

    async def peek_code(self, code_hash: str) -> AuthorizationCodeRecord | None:
        return self.codes.get(code_hash)

    async def take_code(self, code_hash: str, now: datetime) -> AuthorizationCodeRecord | None:
        record = self.codes.get(code_hash)
        if record is None or record.used_at is not None or record.expires_at <= now:
            return None
        used = AuthorizationCodeRecord(
            code_hash=record.code_hash,
            grant_id=record.grant_id,
            client_id=record.client_id,
            subject=record.subject,
            scopes=record.scopes,
            code_challenge=record.code_challenge,
            redirect_uri=record.redirect_uri,
            resource=record.resource,
            expires_at=record.expires_at,
            used_at=now,
        )
        self.codes[code_hash] = used
        return used

    # -- tokens -------------------------------------------------------
    async def save_token(self, token: TokenRecord) -> None:
        self.tokens[token.token_hash] = token

    async def get_token(self, token_hash: str) -> TokenRecord | None:
        return self.tokens.get(token_hash)

    async def revoke_token(self, token_hash: str, revoked_at: datetime) -> None:
        record = self.tokens.get(token_hash)
        if record is not None and record.revoked_at is None:
            self.tokens[token_hash] = TokenRecord(
                token_hash=record.token_hash,
                grant_id=record.grant_id,
                client_id=record.client_id,
                subject=record.subject,
                kind=record.kind,
                scopes=record.scopes,
                expires_at=record.expires_at,
                resource=record.resource,
                revoked_at=revoked_at,
            )

    async def revoke_tokens_for_grant(self, grant_id: str, revoked_at: datetime) -> int:
        count = 0
        for token_hash, record in list(self.tokens.items()):
            if record.grant_id == grant_id and record.revoked_at is None:
                await self.revoke_token(token_hash, revoked_at)
                count += 1
        return count

    async def delete_expired(self, before: datetime) -> int:
        removed = 0
        for code_hash, record in list(self.codes.items()):
            if record.expires_at < before:
                del self.codes[code_hash]
                removed += 1
        for token_hash, record in list(self.tokens.items()):
            if record.expires_at < before:
                del self.tokens[token_hash]
                removed += 1
        return removed
