"""In-place editing of a connection's permissions (service layer).

The claims these tests pin, and why each one matters:

* an edit is **not** a re-consent — it must not resume a paused connection,
  which is the regression a dedicated ``update_grant_binding`` repository method
  exists to prevent;
* editing cannot touch another user's connection;
* an unknown or empty scope set is refused, never silently narrowed;
* adding ``documents.delete`` needs an explicit confirmation, removing it does
  not;
* narrowing the grant immediately invalidates a wider token the agent already
  holds (the claim the settings UI will make).

Style: like the rest of this repository's async tests, these are **sync** tests
driving a coroutine with ``asyncio.run`` — pytest-asyncio's async fixtures crash
on pytest 9 here.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.application.exceptions.mcp_exceptions import (
    ConnectionEditError,
    ConnectionStateError,
    InvalidScopeError,
)
from src.application.ports.mcp_oauth_port import TokenRecord
from src.application.services.mcp_access_service import (
    ACCESS_TOKEN_KIND,
    MCPAccessService,
    hash_secret,
)
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import AgentScope
from tests.fakes.fake_mcp_repo import FakeMCPRepository

RESOURCE = "https://api.example.test/mcp"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

READ = AgentScope.DOCUMENTS_READ
CONVERT = AgentScope.DOCUMENTS_CONVERT
WRITE = AgentScope.DOCUMENTS_WRITE
DELETE = AgentScope.DOCUMENTS_DELETE


def _service(repo: FakeMCPRepository) -> MCPAccessService:
    return MCPAccessService(repository=repo, resource_url=RESOURCE, clock=lambda: NOW)


async def _seed_grant(
    repo: FakeMCPRepository,
    *,
    user_id: int = 7,
    grant_id: str = "grant-1",
    scopes: tuple[AgentScope, ...] = (READ, CONVERT),
    status: AgentGrantStatus = AgentGrantStatus.ACTIVE,
    paused_at: datetime | None = None,
    folder_access: FolderAccess = FolderAccess.ALL,
    folder_id: str | None = None,
) -> AgentGrant:
    grant = AgentGrant(
        id=grant_id,
        user_id=user_id,
        client_id="client-1",
        client_name="Example Agent",
        scopes=scopes,
        status=status,
        resource=RESOURCE,
        folder_access=folder_access,
        folder_id=folder_id,
        history_scope=HistoryScope.AGENT,
        created_at=NOW,
        paused_at=paused_at,
    )
    repo.grants[grant.id] = grant
    return grant


async def _issue_access_token(
    repo: FakeMCPRepository, grant: AgentGrant, token: str
) -> None:
    await repo.save_token(
        TokenRecord(
            token_hash=hash_secret(token),
            grant_id=grant.id,
            client_id=grant.client_id,
            subject=str(grant.user_id),
            kind=ACCESS_TOKEN_KIND,
            scopes=grant.scopes,
            expires_at=NOW + timedelta(hours=1),
            resource=RESOURCE,
        )
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_update_replaces_the_binding_and_returns_the_stored_grant() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo)
        updated = await _service(repo).update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read", "documents.write"],
            folder_access=FolderAccess.FOLDER,
            folder_id="folder-a",
            history_scope=HistoryScope.ALL,
        )
        assert updated is not None
        assert updated.scopes == (READ, WRITE)
        assert updated.folder_access is FolderAccess.FOLDER
        assert updated.folder_id == "folder-a"
        assert updated.history_scope is HistoryScope.ALL
        assert updated.status is AgentGrantStatus.ACTIVE

        # Read back through the repository, not just the returned object.
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert stored.scopes == (READ, WRITE)
        assert stored.folder_id == "folder-a"
        assert stored.history_scope is HistoryScope.ALL

    asyncio.run(_run())


def test_whole_drive_edit_clears_a_previously_bound_folder() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(
            repo, folder_access=FolderAccess.FOLDER, folder_id="folder-a"
        )
        updated = await _service(repo).update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert updated is not None
        assert updated.folder_access is FolderAccess.ALL
        assert updated.folder_id is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Lifecycle is untouched by an edit
# ---------------------------------------------------------------------------


def test_a_paused_connection_stays_paused_after_an_edit() -> None:
    """The regression the dedicated repository method exists to prevent."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(
            repo, status=AgentGrantStatus.PAUSED, paused_at=NOW
        )
        updated = await _service(repo).update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert updated is not None
        assert updated.status is AgentGrantStatus.PAUSED
        assert updated.paused_at == NOW
        assert updated.scopes == (READ,)

        # …and directly against the repository's stored state.
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert stored.status is AgentGrantStatus.PAUSED
        assert stored.paused_at == NOW

    asyncio.run(_run())


def test_a_revoked_connection_cannot_be_edited() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(
            repo, status=AgentGrantStatus.REVOKED
        )
        with pytest.raises(ConnectionStateError):
            await _service(repo).update_connected_app(
                user_id=7,
                grant_id=grant.id,
                scopes=["documents.read"],
                folder_access=FolderAccess.ALL,
                folder_id=None,
                history_scope=HistoryScope.AGENT,
            )
        # Nothing was written.
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert stored.status is AgentGrantStatus.REVOKED
        assert stored.scopes == (READ, CONVERT)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_another_users_connection_cannot_be_edited() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo, user_id=7)
        result = await _service(repo).update_connected_app(
            user_id=8,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert result is None
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert stored.scopes == (READ, CONVERT)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------


def test_an_empty_scope_set_is_refused() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo)
        with pytest.raises(ConnectionEditError):
            await _service(repo).update_connected_app(
                user_id=7,
                grant_id=grant.id,
                scopes=[],
                folder_access=FolderAccess.ALL,
                folder_id=None,
                history_scope=HistoryScope.AGENT,
            )

    asyncio.run(_run())


def test_an_unknown_scope_is_refused_rather_than_dropped() -> None:
    """A silent drop would persist a narrowed set the user never asked for."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo)
        with pytest.raises(InvalidScopeError):
            await _service(repo).update_connected_app(
                user_id=7,
                grant_id=grant.id,
                scopes=["documents.read", "documents.admin"],
                folder_access=FolderAccess.ALL,
                folder_id=None,
                history_scope=HistoryScope.AGENT,
            )
        # The grant is unchanged: a valid subset was NOT silently stored.
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert stored.scopes == (READ, CONVERT)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Destructive-scope confirmation
# ---------------------------------------------------------------------------


def test_adding_delete_requires_confirmation() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo)
        with pytest.raises(ConnectionEditError):
            await _service(repo).update_connected_app(
                user_id=7,
                grant_id=grant.id,
                scopes=["documents.read", "documents.delete"],
                folder_access=FolderAccess.ALL,
                folder_id=None,
                history_scope=HistoryScope.AGENT,
            )
        stored = await repo.get_grant(grant.id)
        assert stored is not None
        assert DELETE not in stored.scopes

        confirmed = await _service(repo).update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read", "documents.delete"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
            confirm_destructive=True,
        )
        assert confirmed is not None
        assert DELETE in confirmed.scopes

    asyncio.run(_run())


def test_removing_delete_needs_no_confirmation() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        grant = await _seed_grant(repo, scopes=(READ, DELETE))
        updated = await _service(repo).update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert updated is not None
        assert DELETE not in updated.scopes

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Effect on an already-issued token
# ---------------------------------------------------------------------------


def test_narrowing_invalidates_a_wider_token_the_agent_already_holds() -> None:
    """The claim the UI makes: narrowing bites on the agent's next call."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        grant = await _seed_grant(repo, scopes=(READ, CONVERT, DELETE))
        await _issue_access_token(repo, grant, "wide-token")
        assert await service.load_access_token("wide-token") is not None

        await service.update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert await service.load_access_token("wide-token") is None

    asyncio.run(_run())


def test_a_paused_connection_refuses_its_token_after_an_edit() -> None:
    """Editing a paused connection must not accidentally bring its token back."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        grant = await _seed_grant(repo, scopes=(READ, CONVERT))
        await _issue_access_token(repo, grant, "token-abc")
        await service.pause_connection(7, grant.id)
        assert await service.load_access_token("token-abc") is None

        await service.update_connected_app(
            user_id=7,
            grant_id=grant.id,
            scopes=["documents.read"],
            folder_access=FolderAccess.ALL,
            folder_id=None,
            history_scope=HistoryScope.AGENT,
        )
        assert await service.load_access_token("token-abc") is None

    asyncio.run(_run())
