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
from src.application.services.mcp.access_service import MCPAccessService
from src.domain.security.enitities.agent_grant import AgentGrantStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import (
    ALL_SCOPES,
    DEFAULT_SCOPES,
    OFFLINE_ACCESS,
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


# ---------------------------------------------------------------------------
# Folder binding and history scope
# ---------------------------------------------------------------------------


def test_the_consent_view_defaults_to_whole_drive_and_agent_history() -> None:
    """No prior grant means the least-access binding, not a remembered one."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        view = await _service(repo).describe_authorization(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            resource=RESOURCE,
        )
        assert view.folder_access is FolderAccess.ALL
        assert view.folder_id is None
        assert view.history_scope is HistoryScope.AGENT

    asyncio.run(_run())


def test_approve_persists_the_folder_binding_and_history_scope() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        await service.approve(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            code_challenge="challenge",
            resource=RESOURCE,
            folder_access=FolderAccess.FOLDER,
            folder_id="folder-a",
            history_scope=HistoryScope.ALL,
        )
        grant = await repo.get_grant_for_user_client(7, "client-1")
        assert grant is not None
        assert grant.folder_access is FolderAccess.FOLDER
        assert grant.folder_id == "folder-a"
        assert grant.history_scope is HistoryScope.ALL

    asyncio.run(_run())


def test_describe_reports_the_existing_binding_rather_than_resetting_it() -> None:
    """A re-consent screen must show what the user already chose."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        await service.approve(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            code_challenge="challenge",
            resource=RESOURCE,
            folder_access=FolderAccess.FOLDER,
            folder_id="folder-a",
            history_scope=HistoryScope.ALL,
        )
        view = await service.describe_authorization(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            resource=RESOURCE,
        )
        assert view.folder_access is FolderAccess.FOLDER
        assert view.folder_id == "folder-a"
        assert view.history_scope is HistoryScope.ALL

    asyncio.run(_run())


def test_re_consent_replaces_the_previous_binding() -> None:
    """Moving from folder A to B must take effect, not merge with A."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        service = _service(repo)
        await _register(repo)
        await service.approve(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            code_challenge="challenge",
            resource=RESOURCE,
            folder_access=FolderAccess.FOLDER,
            folder_id="folder-a",
            history_scope=HistoryScope.AGENT,
        )
        await service.approve(
            user_id=7,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read"],
            code_challenge="challenge",
            resource=RESOURCE,
            folder_access=FolderAccess.FOLDER,
            folder_id="folder-b",
            history_scope=HistoryScope.ALL,
        )
        grant = await repo.get_grant_for_user_client(7, "client-1")
        assert grant is not None
        assert grant.folder_id == "folder-b"
        assert grant.history_scope is HistoryScope.ALL

    asyncio.run(_run())


# --------------------------------------------------------------------------
# The reason a scope is refused
#
# WHY: all three refusals used to share one sentence, "Unsupported or disallowed
# scope(s)". When a client that had omitted ``scope`` at registration asked for
# workspace access, that read as "this product has no workspaces" — while the
# server advertised five scopes and served nine workspace tools. Only one of the
# three is something the user can act on, so only one may be worded that way.
# --------------------------------------------------------------------------


def test_a_client_outside_its_registration_is_told_it_is_the_client() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        # A client that capped itself to read at registration.
        await _register(repo, scope="documents.read")
        with pytest.raises(InvalidScopeError) as caught:
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["workspaces.read"],
                resource=RESOURCE,
            )
        message = str(caught.value)
        # It must point at the client's registration, not call the scope unknown.
        assert "did not register for" in message
        assert "workspaces.read" in message
        assert "Unknown scope" not in message

    asyncio.run(_run())


def test_a_genuinely_unknown_scope_lists_the_supported_ones() -> None:
    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo)
        with pytest.raises(InvalidScopeError) as caught:
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["workspaces.admin"],
                resource=RESOURCE,
            )
        message = str(caught.value)
        assert "Unknown scope" in message
        # The whole vocabulary, so the caller can correct itself in one step.
        for scope in ("documents.read", "documents.delete", "workspaces.execute"):
            assert scope in message

    asyncio.run(_run())


def test_an_unrestricted_client_may_be_granted_every_scope() -> None:
    """The regression this pins.

    A client that registers without a ``scope`` (what most MCP clients do, since
    RFC 7591 makes it optional) must not end up unable to be granted the scopes
    the server advertises. Before the fix, the registration filled in the
    *consent default* — read/convert/write — and that was then treated as the
    client's own cap, so ``documents.delete`` and both ``workspaces.*`` scopes
    were permanently ungrantable during consent.
    """

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo, scope=None)
        described = await _service(repo).describe_authorization(
            user_id=1,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=[scope.value for scope in ALL_SCOPES],
            resource=RESOURCE,
        )
        # Every scope the server advertises is grantable to a client that did not
        # restrict itself.
        assert {scope.value for scope in described.requested_scopes} == {
            scope.value for scope in ALL_SCOPES
        }

    asyncio.run(_run())


# --------------------------------------------------------------------------
# offline_access — a request MARKER, never a capability
#
# WHY: clients that omit it from `scopes_supported`-based discovery conclude
# refresh is unavailable and drop the connection when the 60-minute access token
# expires, even though a refresh token was issued. But simply advertising it
# would have broken EVERY authorization request, because `resolve_scopes` refuses
# an unrecognised scope (fail closed). Both halves are required, and the second
# half is the constraint that matters: accepting it must not make it grantable.
# --------------------------------------------------------------------------


def test_offline_access_is_accepted_in_a_request() -> None:
    """Advertising a scope the resolver rejects would fail every request that used it."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo, scope=None)
        described = await _service(repo).describe_authorization(
            user_id=1,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read", OFFLINE_ACCESS],
            resource=RESOURCE,
        )
        # The request is valid, and the marker is not one of the permissions.
        assert [scope.value for scope in described.requested_scopes] == ["documents.read"]

    asyncio.run(_run())


def test_offline_access_is_never_granted() -> None:
    """The invariant: a marker is accepted on input and can never be stored.

    A grant row listing `offline_access` would claim the agent holds a permission
    this product does not model, and the consent screen would offer a checkbox
    that grants nothing.
    """

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo, scope=None)
        code = await _service(repo).approve(
            user_id=1,
            client_id="client-1",
            redirect_uri=REDIRECT,
            requested_scopes=["documents.read", OFFLINE_ACCESS],
            code_challenge="challenge",
            resource=RESOURCE,
            state="s",
        )
        assert "code=" in code
        grant = await repo.get_grant_for_user_client(1, "client-1")
        assert grant is not None
        granted = {scope.value for scope in grant.scopes}
        assert granted == {"documents.read"}
        assert OFFLINE_ACCESS not in granted

        # And it cannot be smuggled in as the only scope either: an empty
        # capability set is a refusal, because requesting nothing grantable is
        # not a way to obtain something grantable.
        assert OFFLINE_ACCESS not in {scope.value for scope in ALL_SCOPES}

    asyncio.run(_run())


def test_offline_access_is_accepted_in_a_client_registration() -> None:
    """A client that registers FOR it must not be rejected at registration."""

    def _run() -> None:
        from src.presentation.mcp.server import (
            build_authorization_server_metadata,
        )

        metadata = build_authorization_server_metadata()
        # The SDK derives `scopes_supported` from the registration options, so
        # this asserts the wiring end to end rather than the constant.
        assert OFFLINE_ACCESS in metadata["scopes_supported"]
        assert "documents.read" in metadata["scopes_supported"]
        assert "workspaces.execute" in metadata["scopes_supported"]

    _run()


def test_an_unknown_scope_is_still_refused_alongside_the_marker() -> None:
    """Accepting the marker must not loosen the fail-closed rule for anything else."""

    async def _run() -> None:
        repo = FakeMCPRepository()
        await _register(repo, scope=None)
        with pytest.raises(InvalidScopeError) as caught:
            await _service(repo).describe_authorization(
                user_id=1,
                client_id="client-1",
                redirect_uri=REDIRECT,
                requested_scopes=["documents.read", OFFLINE_ACCESS, "documents.admin"],
                resource=RESOURCE,
            )
        # Only the genuinely unknown value is named.
        assert "documents.admin" in str(caught.value)
        assert OFFLINE_ACCESS not in str(caught.value)

    asyncio.run(_run())


def test_the_consent_view_lists_every_permission_including_workspaces() -> None:
    """The regression this pins.

    `_scope_descriptions` hand-listed only the four `documents.*` scopes, so the
    consent screen never rendered the workspace permissions. Since the screen
    posts back only what it rendered, an approved connection silently LOST any
    workspace access the client asked for — and the user could not see that it had
    been requested. An agent then failed every workspace tool with "needs the
    workspaces.read permission", with no way for the user to grant it.
    """

    def _run() -> None:
        from src.presentation.api.routers.v1.mcp import _scope_descriptions

        described = _scope_descriptions((), ())
        listed = [item.scope for item in described]
        assert listed == [scope.value for scope in ALL_SCOPES], (
            "the consent screen must offer exactly the product's permissions"
        )
        assert "workspaces.read" in listed
        assert "workspaces.execute" in listed
        # Every listed scope has user-facing wording, or the screen would print a
        # raw identifier.
        assert all(item.description for item in described)
        # And none of them is a request marker.
        assert OFFLINE_ACCESS not in listed

    _run()
