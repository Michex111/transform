"""The MCP access rules: scopes, redirect matching, audience binding, revocation.

These are the tests that matter most for this feature, because every one of them
pins a way a document could leak or an agent could exceed its consent.

Style note: like the rest of this repository's async tests, these are **sync**
tests that drive a coroutine with ``asyncio.run`` — pytest-asyncio's async
fixtures crash on pytest 9 here.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.application.exceptions.mcp_exceptions import (
    InvalidGrantError,
    InvalidRedirectUriError,
    InvalidScopeError,
    InvalidTargetError,
)
from src.application.ports.mcp_oauth_port import OAuthClientRecord, TokenRecord
from src.application.services.mcp_access_service import MCPAccessService
from src.domain.security.enitities.agent_grant import AgentGrantStatus
from src.domain.security.value_object.agent_scope import (
    DEFAULT_SCOPES,
    AgentScope,
    normalize_scopes,
)
from tests.fakes.fake_mcp_repo import FakeMCPRepository

RESOURCE = "https://api.example.test/mcp"
REDIRECT = "https://agent.example.test/callback"

#: A fixed instant, so expiry tests move the clock deliberately instead of
#: depending on wall-clock time.
_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _service(repo: FakeMCPRepository, *, now: datetime = _NOW) -> MCPAccessService:
    return MCPAccessService(repository=repo, resource_url=RESOURCE, clock=lambda: now)


async def _register(repo: FakeMCPRepository, *, scope: str | None = None) -> str:
    client = OAuthClientRecord(
        client_id="client-1",
        client_name="Test Agent",
        redirect_uris=(REDIRECT,),
        scope=scope,
    )
    await repo.save_client(client)
    return client.client_id


async def _approve(
    repo: FakeMCPRepository,
    *,
    user_id: int = 7,
    scopes: tuple[str, ...] = ("documents.read",),
    resource: str | None = RESOURCE,
    now: datetime = _NOW,
) -> str:
    """Approve consent for ``client-1`` and return the authorization code."""
    url = await _service(repo, now=now).approve(
        user_id=user_id,
        client_id="client-1",
        redirect_uri=REDIRECT,
        requested_scopes=list(scopes),
        code_challenge="challenge",
        resource=resource,
    )
    return url.split("code=", 1)[1].split("&", 1)[0]


async def _grant_and_token(
    repo: FakeMCPRepository,
    *,
    user_id: int = 7,
) -> tuple[str, str]:
    """Register a client, approve consent and redeem the code.

    Returns ``(grant_id, access_token)``.
    """
    service = _service(repo)
    await _register(repo)
    code = await _approve(repo)
    issued = await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)
    grant = await repo.get_grant_for_user_client(user_id, "client-1")
    assert grant is not None
    return grant.id, issued.access_token


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


def test_an_unknown_scope_is_refused_rather_than_dropped() -> None:
    """Dropping an unknown scope silently is how a default grant becomes a widening."""
    assert normalize_scopes(["documents.read", "documents.admin"]) == (
        AgentScope.DOCUMENTS_READ,
    )


def test_the_default_grant_never_includes_delete() -> None:
    assert AgentScope.DOCUMENTS_DELETE not in DEFAULT_SCOPES


def test_a_client_cannot_request_a_scope_it_never_registered() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo, scope="documents.read")
        with pytest.raises(InvalidScopeError):
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["documents.delete"],
                resource=RESOURCE,
            )

    asyncio.run(_run())


def test_an_unknown_scope_string_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        with pytest.raises(InvalidScopeError):
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["documents.*"],
                resource=RESOURCE,
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Redirect URI and audience
# ---------------------------------------------------------------------------


def test_a_near_miss_redirect_uri_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        with pytest.raises(InvalidRedirectUriError):
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri="https://agent.example.test/callback/",
                requested_scopes=["documents.read"],
                resource=RESOURCE,
            )

    asyncio.run(_run())


def test_a_foreign_resource_indicator_is_refused() -> None:
    """Minting a token for another audience is the token-passthrough failure."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        with pytest.raises(InvalidTargetError):
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["documents.read"],
                resource="https://evil.example.test/mcp",
            )

    asyncio.run(_run())


def test_an_absent_resource_binds_to_this_server() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        view = await _service(repo).describe_authorization(
            user_id=1,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            resource=None,
        )
        assert view.resource == RESOURCE

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Codes and tokens
# ---------------------------------------------------------------------------


def test_approval_requires_a_pkce_challenge() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        with pytest.raises(InvalidGrantError):
            await _service(repo).approve(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["documents.read"],
                code_challenge="",
                resource=RESOURCE,
            )

    asyncio.run(_run())


def test_an_authorization_code_can_only_be_redeemed_once() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        code = await _approve(repo)
        service = _service(repo)
        first = await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)
        assert first.access_token
        with pytest.raises(InvalidGrantError):
            await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)

    asyncio.run(_run())


def test_a_code_cannot_be_redeemed_by_a_different_client() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        code = await _approve(repo)
        with pytest.raises(InvalidGrantError):
            await _service(repo).exchange_code(
                client_id="someone-else", code=code, redirect_uri=None
            )

    asyncio.run(_run())


def test_an_expired_code_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        code = await _approve(repo)
        later = _service(repo, now=_NOW + timedelta(minutes=10))
        with pytest.raises(InvalidGrantError):
            await later.exchange_code(client_id="client-1", code=code, redirect_uri=None)

    asyncio.run(_run())


def test_an_access_token_is_refused_after_the_grant_is_revoked() -> None:
    """Revocation must bite immediately, not at token expiry."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        grant_id, token = await _grant_and_token(repo)
        service = _service(repo)
        assert await service.load_access_token(token) is not None

        revoked = await service.revoke_connection(7, grant_id)
        assert revoked is not None
        assert revoked.status is AgentGrantStatus.REVOKED
        assert await service.load_access_token(token) is None

    asyncio.run(_run())


def test_revoking_a_grant_also_revokes_its_tokens() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant_id, _token = await _grant_and_token(repo)
        await _service(repo).revoke_connection(7, grant_id)
        assert repo.tokens
        assert all(record.revoked_at is not None for record in repo.tokens.values())

    asyncio.run(_run())


def test_a_user_cannot_revoke_another_users_grant() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant_id, _token = await _grant_and_token(repo, user_id=7)
        assert await _service(repo).revoke_connection(8, grant_id) is None
        grant = await repo.get_grant(grant_id)
        assert grant is not None and grant.status is AgentGrantStatus.ACTIVE

    asyncio.run(_run())


def test_narrowing_a_grant_invalidates_a_wider_token() -> None:
    """A re-consent that drops convert must not leave the old token able to convert."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        code = await _approve(repo, scopes=("documents.read", "documents.convert"))
        issued = await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)
        assert await service.load_access_token(issued.access_token) is not None

        await service.approve(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            code_challenge="challenge",
            resource=RESOURCE,
        )
        assert await service.load_access_token(issued.access_token) is None

    asyncio.run(_run())


def test_a_token_issued_for_another_resource_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant_id, token = await _grant_and_token(repo)
        access_hash = next(h for h, r in repo.tokens.items() if r.kind == "ACCESS")
        record = repo.tokens[access_hash]
        # Simulate a token that was minted for a different audience.
        repo.tokens[access_hash] = TokenRecord(
            token_hash=record.token_hash,
            grant_id=grant_id,
            client_id=record.client_id,
            subject=record.subject,
            kind=record.kind,
            scopes=record.scopes,
            expires_at=record.expires_at,
            resource="https://other.example.test/mcp",
        )
        assert await _service(repo).load_access_token(token) is None

    asyncio.run(_run())


def test_loading_a_token_records_last_use() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant_id, token = await _grant_and_token(repo)
        await _service(repo).load_access_token(token)
        assert [entry[0] for entry in repo.touched] == [grant_id]

    asyncio.run(_run())


def test_refresh_rotates_the_refresh_token() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        code = await _approve(repo)
        first = await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)
        assert first.refresh_token

        second = await service.exchange_refresh(
            client_id="client-1", refresh_token=first.refresh_token, resource=RESOURCE
        )
        assert second.refresh_token != first.refresh_token

        with pytest.raises(InvalidGrantError):
            await service.exchange_refresh(
                client_id="client-1", refresh_token=first.refresh_token, resource=RESOURCE
            )

    asyncio.run(_run())


def test_refresh_cannot_widen_the_scope_set() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        code = await _approve(repo)
        issued = await service.exchange_code(client_id="client-1", code=code, redirect_uri=None)
        with pytest.raises(InvalidScopeError):
            await service.exchange_refresh(
                client_id="client-1",
                refresh_token=issued.refresh_token or "",
                scopes=["documents.read", "documents.delete"],
                resource=RESOURCE,
            )

    asyncio.run(_run())


def test_an_expired_access_token_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        _grant_id, token = await _grant_and_token(repo)
        later = _service(repo, now=_NOW + timedelta(hours=2))
        assert await later.load_access_token(token) is None

    asyncio.run(_run())
