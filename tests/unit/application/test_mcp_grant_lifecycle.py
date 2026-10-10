"""Tests for MCP connection lifecycle: pause, resume and revoke.

This is the security-critical surface of the MCP Activity page, so these tests
target the *enforcement*, not the UI: what happens to an already-issued access
token when a connection is paused, that one user's pause cannot touch another
user's grant, that revocation is terminal, and that resuming can never widen the
permissions that were originally consented to.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.application.exceptions.mcp_exceptions import ConnectionStateError
from src.application.ports.mcp_oauth_port import TokenRecord
from src.application.services.mcp_access_service import (
    ACCESS_TOKEN_KIND,
    MCPAccessService,
    hash_secret,
)
from src.domain.security.enitities.agent_grant import AgentGrant, AgentGrantStatus
from src.domain.security.exceptions.exceptions import InvalidGrantTransition
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import AgentScope
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
RESOURCE = "https://api.example.test/mcp"


@contextmanager
def sqlite_session_factory():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _seed_user(factory, username: str) -> int:
    async with factory() as session:
        user = UserModel(
            username=username, email=f"{username}@example.com", hashed_password="x", is_active=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


def _service(session) -> MCPAccessService:
    return MCPAccessService(
        repository=SQLMCPRepository(session),
        resource_url=RESOURCE,
        clock=lambda: NOW,
    )


def _store(service: MCPAccessService) -> SQLMCPRepository:
    """The repository behind a service, for arranging test state directly.

    Reachable only through the private attribute, so it is confined to this one
    helper: the tests that *assert* behaviour go through the service, and only
    seeding reads/writes the store.
    """
    return cast(SQLMCPRepository, service._repository)  # noqa: SLF001


async def _grant(
    service: MCPAccessService,
    *,
    user_id: int,
    client_id: str = "client-1",
    scopes: tuple[AgentScope, ...] = (AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_WRITE),
) -> AgentGrant:
    return await _store(service).upsert_grant(
        AgentGrant(
            id=f"grant-{user_id}-{client_id}",
            user_id=user_id,
            client_id=client_id,
            client_name="Example Agent",
            scopes=scopes,
            status=AgentGrantStatus.ACTIVE,
            resource=RESOURCE,
            created_at=NOW,
        )
    )


async def _issue_token(service: MCPAccessService, grant: AgentGrant, token: str) -> None:
    await _store(service).save_token(
        TokenRecord(
            token_hash=hash_secret(token),
            grant_id=grant.id,
            client_id=grant.client_id,
            subject=str(grant.user_id),
            kind=ACCESS_TOKEN_KIND,
            scopes=grant.scopes,
            resource=RESOURCE,
            expires_at=NOW + timedelta(hours=1),
        )
    )


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------


def test_pause_blocks_an_already_issued_access_token() -> None:
    """The enforcement that matters: a live token stops working immediately."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "pauser")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account)
                await _issue_token(service, grant, "token-abc")

                # Before the pause the token authenticates.
                assert await service.load_access_token("token-abc") is not None

                paused = await service.pause_connection(account, grant.id)

                assert paused is not None
                assert paused.status is AgentGrantStatus.PAUSED
                assert paused.paused_at == NOW
                assert not paused.is_active()
                # The very next call is refused — no expiry window to wait out.
                assert await service.load_access_token("token-abc") is None

        asyncio.run(_run())


def test_pause_is_idempotent_and_keeps_the_original_timestamp() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "idem")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account)

                first = await service.pause_connection(account, grant.id)
                second = await service.pause_connection(account, grant.id)

            assert first is not None and second is not None
            assert second.status is AgentGrantStatus.PAUSED
            # The audit trail keeps when access actually changed.
            assert second.paused_at == first.paused_at == NOW

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_resume_restores_access_without_widening_scopes() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "resumer")
            original = (AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_CONVERT)
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account, scopes=original)
                await _issue_token(service, grant, "token-abc")

                await service.pause_connection(account, grant.id)
                assert await service.load_access_token("token-abc") is None

                resumed = await service.resume_connection(account, grant.id)

            assert resumed is not None
            assert resumed.status is AgentGrantStatus.ACTIVE
            assert resumed.paused_at is None
            # Exactly the scopes that were consented to — `documents.delete` and
            # `documents.write` were never granted and must not appear.
            assert set(resumed.scopes) == set(original)

        asyncio.run(_run())


def test_resume_does_not_resurrect_a_narrowed_scope_set() -> None:
    """A resume restores what is stored, never what once was."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "narrowed")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(
                    service,
                    user_id=account,
                    scopes=(AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_DELETE),
                )
                await service.pause_connection(account, grant.id)

                # A re-consent that drops `delete` while the grant is paused.
                await _grant(
                    service,
                    user_id=account,
                    scopes=(AgentScope.DOCUMENTS_READ,),
                )
                resumed = await service.resume_connection(account, grant.id)

            assert resumed is not None
            assert set(resumed.scopes) == {AgentScope.DOCUMENTS_READ}

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoke_invalidates_tokens_and_is_terminal() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "revoker")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account)
                await _issue_token(service, grant, "token-abc")

                revoked = await service.revoke_connection(account, grant.id)
                assert revoked is not None
                assert revoked.status is AgentGrantStatus.REVOKED
                assert await service.load_access_token("token-abc") is None

                # Revocation is terminal: neither pause nor resume may revive it.
                with pytest.raises(ConnectionStateError):
                    await service.pause_connection(account, grant.id)
                with pytest.raises(ConnectionStateError):
                    await service.resume_connection(account, grant.id)

        asyncio.run(_run())


def test_grant_entity_refuses_to_transition_a_revoked_grant() -> None:
    # The rule enforced in SQL is also enforced in the entity, so a stale
    # in-memory instance cannot be the weak link.
    grant = AgentGrant(
        id="g",
        user_id=1,
        client_id="c",
        client_name="x",
        scopes=(AgentScope.DOCUMENTS_READ,),
        status=AgentGrantStatus.REVOKED,
        revoked_at=NOW,
    )
    with pytest.raises(InvalidGrantTransition):
        grant.pause(now=NOW)
    with pytest.raises(InvalidGrantTransition):
        grant.resume()


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_pausing_another_users_grant_is_impossible() -> None:
    """One user's pause must not touch another user's connection."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            alice = await _seed_user(factory, "alice")
            bob = await _seed_user(factory, "bob")
            async with factory() as session:
                service = _service(session)
                # The same client application, authorized by both users.
                alice_grant = await _grant(service, user_id=alice)
                bob_grant = await _grant(service, user_id=bob)

                # Bob tries to pause Alice's connection by guessing its id.
                result = await service.pause_connection(bob, alice_grant.id)

                assert result is None
                # Alice's connection is untouched.
                still = await _store(service).get_grant(alice_grant.id)
                assert still is not None
                assert still.status is AgentGrantStatus.ACTIVE

                # And Bob's own pause only affects Bob.
                paused = await service.pause_connection(bob, bob_grant.id)
                assert paused is not None
                alice_still = await _store(service).get_grant(alice_grant.id)
                assert alice_still is not None
                assert alice_still.status is AgentGrantStatus.ACTIVE

        asyncio.run(_run())


def test_revoking_another_users_grant_is_impossible() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            alice = await _seed_user(factory, "alice2")
            bob = await _seed_user(factory, "bob2")
            async with factory() as session:
                service = _service(session)
                alice_grant = await _grant(service, user_id=alice)
                await _issue_token(service, alice_grant, "alice-token")

                assert await service.revoke_connection(bob, alice_grant.id) is None
                # Alice's credentials still work: the failed attempt changed
                # nothing.
                assert await service.load_access_token("alice-token") is not None

        asyncio.run(_run())


def test_scoping_narrows_a_token_after_a_re_consent() -> None:
    """A token minted before a scope reduction is refused, not downgraded."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "scoped")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(
                    service,
                    user_id=account,
                    scopes=(AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_DELETE),
                )
                await _issue_token(service, grant, "wide-token")

                # Re-consent without `delete`.
                await _grant(service, user_id=account, scopes=(AgentScope.DOCUMENTS_READ,))

                assert await service.load_access_token("wide-token") is None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# In-place binding edit (the method that must NOT reactivate a grant)
# ---------------------------------------------------------------------------


async def _update_binding(
    store: SQLMCPRepository,
    *,
    grant_id: str,
    user_id: int,
    scopes: tuple[AgentScope, ...] = (AgentScope.DOCUMENTS_READ,),
    folder_access: FolderAccess = FolderAccess.FOLDER,
    folder_id: str | None = "folder-a",
    history_scope: HistoryScope = HistoryScope.ALL,
) -> AgentGrant | None:
    return await store.update_grant_binding(
        grant_id=grant_id,
        user_id=user_id,
        scopes=scopes,
        folder_access=folder_access,
        folder_id=folder_id,
        history_scope=history_scope,
    )


def test_update_grant_binding_changes_only_the_binding_fields() -> None:
    """The stored grant keeps its identity, lifecycle and timestamps."""

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "binder")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account)

                updated = await _update_binding(
                    _store(service), grant_id=grant.id, user_id=account
                )

            assert updated is not None
            assert updated.scopes == (AgentScope.DOCUMENTS_READ,)
            assert updated.folder_access is FolderAccess.FOLDER
            assert updated.folder_id == "folder-a"
            assert updated.history_scope is HistoryScope.ALL
            # Everything else is exactly as it was.
            assert updated.status is AgentGrantStatus.ACTIVE
            assert updated.client_id == grant.client_id
            assert updated.client_name == grant.client_name
            assert updated.resource == grant.resource
            assert updated.created_at == grant.created_at
            assert updated.paused_at is None
            assert updated.revoked_at is None

        asyncio.run(_run())


def test_update_grant_binding_keeps_a_paused_grant_paused() -> None:
    """Editing a permission must not resume a suspended connection.

    This is the exact bug that using ``upsert_grant`` for an edit would
    introduce: it sets ``status = ACTIVE`` and clears ``paused_at``.
    """

    with sqlite_session_factory() as factory:

        async def _run() -> None:
            account = await _seed_user(factory, "still-paused")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=account)
                paused = await service.pause_connection(account, grant.id)
                assert paused is not None and paused.paused_at == NOW

                updated = await _update_binding(
                    _store(service), grant_id=grant.id, user_id=account
                )
                stored = await _store(service).get_grant(grant.id)

            assert updated is not None
            assert updated.status is AgentGrantStatus.PAUSED
            assert updated.paused_at == NOW
            assert stored is not None
            assert stored.status is AgentGrantStatus.PAUSED
            assert stored.paused_at == NOW
            # The binding did change, so the update was not simply skipped.
            assert stored.scopes == (AgentScope.DOCUMENTS_READ,)

        asyncio.run(_run())


def test_update_grant_binding_returns_none_for_a_foreign_grant() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            alice = await _seed_user(factory, "owner")
            bob = await _seed_user(factory, "intruder")
            async with factory() as session:
                service = _service(session)
                grant = await _grant(service, user_id=alice)

                result = await _update_binding(
                    _store(service), grant_id=grant.id, user_id=bob
                )
                stored = await _store(service).get_grant(grant.id)

            assert result is None
            assert stored is not None
            assert stored.user_id == alice
            # Alice's binding was not touched by Bob's attempt.
            assert stored.scopes == grant.scopes
            assert stored.folder_access is FolderAccess.ALL
            assert stored.folder_id is None

        asyncio.run(_run())
