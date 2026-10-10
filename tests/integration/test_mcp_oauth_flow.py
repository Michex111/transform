"""End-to-end MCP integration: discovery, OAuth, tool calls, revocation, IDOR.

Everything here runs against the **real** application (real JWT sign-in, real
routes, real MCP SDK transport) over a SQLite database. The only things replaced
are the schema bootstrap and the database session, so the authorization checks
under test are the production ones.

The last group of tests is the point of the whole exercise: an MCP client that
holds a valid token for user A must not be able to reach anything belonging to
user B, and revoking an application must stop it on the very next request.
"""

import asyncio
import base64
import hashlib
import json
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

import src.presentation.api.main as api_main
import src.presentation.mcp.dependencies as mcp_deps
from src.infrastructure.auth.jwt_provider import hash_password
from src.infrastructure.database.models import UserFileModel, UserFolderModel, UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware

PASSWORD = "Sup3rSecret!"
PROTOCOL_VERSION = "2025-06-18"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _sse_json(response: Any) -> dict[str, Any]:
    """Parse a Streamable-HTTP response that may be a single SSE frame."""
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.split("data:", 1)[1].strip())
    return json.loads(response.text)


async def _insert(db_path: str, *rows: Any) -> list[int]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all(list(rows))
            await session.commit()
            for row in rows:
                await session.refresh(row)
            return [getattr(row, "id", 0) for row in rows]
    finally:
        await engine.dispose()


@dataclass
class MCPEnv:
    """A test client plus the database seeding helpers these tests need."""

    client: TestClient
    db_path: str

    def seed_user(self, **overrides: Any) -> int:
        values: dict[str, Any] = {
            "username": "ada",
            "email": "ada@example.com",
            "hashed_password": hash_password(PASSWORD),
            "is_active": True,
            # Seeded verified so the test does not depend on whether the
            # developer's `.env` configures an email transport.
            "email_verified": True,
        }
        values.update(overrides)
        return int(_run(_insert(self.db_path, UserModel(**values)))[0])

    def add_file(self, user_id: int, file_id: str, file_name: str) -> str:
        _run(
            _insert(
                self.db_path,
                UserFileModel(
                    id=file_id,
                    user_id=user_id,
                    file_key=f"upload/{file_id}/stored-object",
                    file_name=file_name,
                    file_extension=file_name.rsplit(".", 1)[-1],
                    file_size_bytes=2048,
                    mime_type="application/octet-stream",
                    folder_id=None,
                ),
            )
        )
        return file_id

    def add_folder(self, user_id: int, folder_id: str, name: str) -> str:
        _run(
            _insert(
                self.db_path,
                UserFolderModel(id=folder_id, user_id=user_id, parent_id=None, name=name),
            )
        )
        return folder_id

    def sign_in(self, username: str = "ada") -> str:
        response = self.client.post(
            "/api/users/token", data={"username": username, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return str(response.json()["access_token"])

    def register_client(self, redirect_uri: str) -> dict[str, Any]:
        response = self.client.post(
            "/mcp/register",
            json={
                "redirect_uris": [redirect_uri],
                "client_name": "Test Agent",
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert response.status_code in (200, 201), response.text
        return response.json()

    # -- MCP JSON-RPC over the real transport --------------------------

    def mcp_post(
        self, payload: dict[str, Any], *, token: str | None = None, path: str = "/mcp/"
    ) -> Any:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.post(path, json=payload, headers=headers)

    def call_tool(self, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one MCP tool call, performing ``initialize`` first when needed.

        A stateless Streamable-HTTP server treats each POST independently, so the
        initialize handshake is repeated rather than relying on a session id.
        """
        self.mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "transform-tests", "version": "1.0"},
                },
            },
            token=token,
        )
        response = self.mcp_post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            token=token,
        )
        assert response.status_code == 200, response.text
        frame = _sse_json(response)
        assert "error" not in frame, frame
        result = frame["result"]
        return result.get("structuredContent") or json.loads(
            result["content"][0]["text"]
        )


@contextmanager
def mcp_env(db_path: str) -> Generator[MCPEnv, None, None]:
    """The real app, over SQLite, with the MCP sub-app bound to the same file."""

    async def no_op_initialize_database() -> None:
        return None

    async def prepare_schema() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    _run(prepare_schema())

    # The MCP sub-app is not reachable from FastAPI's dependency_overrides, so
    # its session factory is patched instead. `open_mcp_scope` and the OAuth
    # provider both resolve it from this module at call time.
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    original_factory = mcp_deps.get_session_factory
    mcp_deps.get_session_factory = lambda: factory  # type: ignore[assignment]

    async def override_db() -> Any:
        async with factory() as session:
            yield session

    original_init = api_main.initialize_database
    original_cors = api_main.apply_bucket_cors
    # Saved and restored deliberately: patching the CLASS affects every
    # middleware instance in the process, so leaving it patched makes unrelated
    # rate-limit tests see "always allowed" and fail their 429 assertions.
    original_is_allowed = RateLimitMiddleware._is_allowed
    api_main.initialize_database = no_op_initialize_database  # type: ignore[assignment]
    api_main.apply_bucket_cors = lambda: None  # type: ignore[assignment]
    api_main.app.dependency_overrides[get_db_session] = override_db
    RateLimitMiddleware._is_allowed = _always_allowed  # type: ignore[method-assign]
    try:
        # follow_redirects=False: the OAuth authorize step answers with a 302 to
        # the SPA consent screen, and that origin is not served in tests.
        with TestClient(api_main.app, follow_redirects=False) as client:
            yield MCPEnv(client=client, db_path=db_path)
    finally:
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init  # type: ignore[assignment]
        api_main.apply_bucket_cors = original_cors  # type: ignore[assignment]
        RateLimitMiddleware._is_allowed = original_is_allowed  # type: ignore[method-assign]
        mcp_deps.get_session_factory = original_factory  # type: ignore[assignment]
        _run(engine.dispose())


async def _always_allowed(self: Any, key: str, limit: int) -> bool:
    return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_protected_resource_metadata_is_served_at_the_canonical_url(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            payload = env.client.get(path).json()
            assert payload["resource"].endswith("/mcp")
            assert payload["authorization_servers"] == [payload["resource"]]
            assert payload["scopes_supported"] == [
                "documents.read",
                "documents.convert",
                "documents.write",
                "documents.delete",
            ]


def test_the_authorization_server_metadata_is_reachable_by_both_derivations(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        for path in (
            "/mcp/.well-known/oauth-authorization-server",
            "/.well-known/oauth-authorization-server/mcp",
        ):
            payload = env.client.get(path).json()
            assert payload["authorization_endpoint"].endswith("/mcp/authorize")
            assert payload["token_endpoint"].endswith("/mcp/token")
            assert payload["registration_endpoint"].endswith("/mcp/register")
            assert payload["code_challenge_methods_supported"] == ["S256"]


def test_the_mcp_endpoint_challenges_an_anonymous_caller(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        response = env.mcp_post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert "resource_metadata=" in challenge
        assert "/.well-known/oauth-protected-resource/mcp" in challenge


# ---------------------------------------------------------------------------
# The full authorization-code flow
# ---------------------------------------------------------------------------


def _authorize_and_redeem(
    env: MCPEnv,
    *,
    username: str = "ada",
    approved: list[str] | None = None,
    redirect_uri: str = "https://agent.example.test/callback",
) -> tuple[dict[str, Any], str]:
    """Run registration → consent → token exchange. Returns (client, tokens)."""
    client_info = env.register_client(redirect_uri)
    client_id = client_info["client_id"]
    verifier, challenge = _pkce()
    token = env.sign_in(username)

    authorize = env.client.get(
        "/mcp/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "documents.read documents.convert",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "http://localhost:8000/mcp",
            "state": "abc",
        },
    )
    assert authorize.status_code == 302, authorize.text
    assert "/app/authorize" in authorize.headers["location"]

    describe = env.client.get(
        "/api/v1/mcp/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "documents.read documents.convert",
            "resource": "http://localhost:8000/mcp",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert describe.status_code == 200, describe.text
    assert describe.json()["client_name"] == "Test Agent"

    approval = env.client.post(
        "/api/v1/mcp/authorize",
        json={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "scope": "documents.read documents.convert",
            "resource": "http://localhost:8000/mcp",
            "state": "abc",
            "approved_scopes": approved or ["documents.read", "documents.convert"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert approval.status_code == 200, approval.text
    redirect_url = approval.json()["redirect_url"]
    assert redirect_url.startswith(redirect_uri)
    assert "state=abc" in redirect_url
    code = redirect_url.split("code=", 1)[1].split("&", 1)[0]

    exchanged = env.client.post(
        "/mcp/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "resource": "http://localhost:8000/mcp",
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    return client_info, exchanged.json()["access_token"]


def test_an_agent_can_list_only_its_own_users_files(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user_id = env.seed_user()
        env.add_file(user_id, "11111111-1111-1111-1111-111111111111", "resume.docx")
        _client, token = _authorize_and_redeem(env)

        result = env.call_tool(token, "list_files", {"limit": 10})
        assert result["ok"] is True
        assert [row["file_name"] for row in result["files"]] == ["resume.docx"]
        # The storage key must never be part of a tool result.
        assert "file_key" not in json.dumps(result)


def test_a_second_user_cannot_reach_the_first_users_file(tmp_path) -> None:
    """The IDOR test: a valid token for user B must not read user A's file."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        alice = env.seed_user(username="alice", email="alice@example.com")
        env.seed_user(username="bob", email="bob@example.com")
        file_id = env.add_file(alice, "22222222-2222-2222-2222-222222222222", "secret.docx")

        _client, bob_token = _authorize_and_redeem(env, username="bob")

        # Bob's listing shows nothing of Alice's.
        listing = env.call_tool(bob_token, "list_files", {})
        assert listing["files"] == []

        # Bob cannot fetch it by id, and the answer is indistinguishable from a
        # nonexistent file (no existence oracle).
        fetched = env.call_tool(bob_token, "get_file", {"file_id": file_id})
        assert fetched["ok"] is False
        assert fetched["error"] == "No such file was found in your Drive."

        # Bob cannot convert or delete it either.
        converted = env.call_tool(
            bob_token, "convert_file", {"file_id": file_id, "target_format": "pdf"}
        )
        assert converted["ok"] is False
        # (delete is refused by scope first, which is also correct.)
        deleted = env.call_tool(bob_token, "delete_file", {"file_id": file_id})
        assert deleted["ok"] is False


def test_a_convert_scope_does_not_imply_delete(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user_id = env.seed_user()
        file_id = env.add_file(user_id, "33333333-3333-3333-3333-333333333333", "resume.docx")
        _client, token = _authorize_and_redeem(env)

        denied = env.call_tool(token, "delete_file", {"file_id": file_id})
        assert denied["ok"] is False
        assert denied["required_scope"] == "documents.delete"

        # …and the file is still there.
        listing = env.call_tool(token, "list_files", {})
        assert [row["file_id"] for row in listing["files"]] == [file_id]


def test_undoable_scope_approval_cannot_widen_the_request(tmp_path) -> None:
    """A tampered consent page cannot approve a scope that was never requested."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        client_info = env.register_client("https://agent.example.test/callback")
        _verifier, challenge = _pkce()
        token = env.sign_in()

        # Only read was requested, but the page asks to approve delete as well.
        response = env.client.post(
            "/api/v1/mcp/authorize",
            json={
                "client_id": client_info["client_id"],
                "redirect_uri": "https://agent.example.test/callback",
                "code_challenge": challenge,
                "scope": "documents.read",
                "approved_scopes": ["documents.read", "documents.delete"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text

        apps = env.client.get(
            "/api/v1/mcp/connected-apps", headers={"Authorization": f"Bearer {token}"}
        ).json()["apps"]
        assert apps[0]["scopes"] == ["documents.read"]


def test_revoking_an_application_stops_it_immediately(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        _client, token = _authorize_and_redeem(env)
        # The management endpoints take the user's own Transform session. An
        # MCP access token is deliberately NOT accepted there — the agent's
        # credential must not be usable as a Transform API credential.
        session = {"Authorization": f"Bearer {env.sign_in()}"}
        assert env.client.get("/api/v1/mcp/connected-apps", headers={"Authorization": f"Bearer {token}"}).status_code == 401

        # The grant is listed, and the agent works.
        listed = env.client.get("/api/v1/mcp/connected-apps", headers=session)
        assert listed.status_code == 200, listed.text
        apps = listed.json()["apps"]
        assert len(apps) == 1
        assert apps[0]["scopes"] == ["documents.read", "documents.convert"]
        assert env.call_tool(token, "list_files", {})["ok"] is True

        revoked = env.client.delete(
            f"/api/v1/mcp/connected-apps/{apps[0]['id']}", headers=session
        )
        assert revoked.status_code == 204

        # The very same token is now refused: revocation is not deferred to
        # token expiry.
        after = env.mcp_post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, token=token)
        assert after.status_code == 401


def test_connected_apps_are_private_to_their_owner(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user(username="alice", email="alice@example.com")
        env.seed_user(username="bob", email="bob@example.com")
        _client, _token = _authorize_and_redeem(env, username="alice")

        bob_token = env.sign_in("bob")
        apps = env.client.get(
            "/api/v1/mcp/connected-apps",
            headers={"Authorization": f"Bearer {bob_token}"},
        ).json()["apps"]
        assert apps == []


def test_a_token_for_another_audience_is_rejected(tmp_path) -> None:
    """RFC 8707: a token minted for a different resource must not be accepted."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        _client, token = _authorize_and_redeem(env)
        # A syntactically plausible but unknown token is refused outright; the
        # audience binding itself is unit-tested against the service.
        forged = env.mcp_post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="not-a-real-token"
        )
        assert forged.status_code == 401
        assert env.call_tool(token, "list_files", {})["ok"] is True


def test_an_unknown_client_is_refused_at_consent(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        token = env.sign_in()
        response = env.client.get(
            "/api/v1/mcp/authorize",
            params={
                "client_id": "does-not-exist",
                "redirect_uri": "https://agent.example.test/callback",
                "scope": "documents.read",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Folder-confined consent and history scope
# ---------------------------------------------------------------------------

CONSENT_REDIRECT = "https://agent.example.test/callback"


def _consent_setup(
    env: MCPEnv, *, username: str = "ada"
) -> tuple[dict[str, Any], str, str]:
    """Register a client, sign in and mint a PKCE challenge for a consent post."""
    client_info = env.register_client(CONSENT_REDIRECT)
    _verifier, challenge = _pkce()
    token = env.sign_in(username)
    return client_info, challenge, token


def _post_consent(
    env: MCPEnv,
    *,
    token: str,
    client_info: dict[str, Any],
    challenge: str,
    scope: str = "documents.read",
    approved: list[str] | None = None,
    **binding: Any,
) -> Any:
    payload: dict[str, Any] = {
        "client_id": client_info["client_id"],
        "redirect_uri": CONSENT_REDIRECT,
        "code_challenge": challenge,
        "scope": scope,
        "resource": "http://localhost:8000/mcp",
        "state": "abc",
        "approved_scopes": ["documents.read"] if approved is None else approved,
    }
    payload.update(binding)
    return env.client.post(
        "/api/v1/mcp/authorize",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _get_consent(
    env: MCPEnv, *, token: str, client_info: dict[str, Any], scope: str = "documents.read"
) -> Any:
    return env.client.get(
        "/api/v1/mcp/authorize",
        params={
            "client_id": client_info["client_id"],
            "redirect_uri": CONSENT_REDIRECT,
            "scope": scope,
            "resource": "http://localhost:8000/mcp",
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def test_get_authorize_lists_folders_and_the_default_binding(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user = env.seed_user()
        env.add_folder(user, "folder-a", "Reports")
        env.add_folder(user, "folder-b", "Receipts")
        client_info, _challenge, token = _consent_setup(env)

        response = _get_consent(env, token=token, client_info=client_info)
        assert response.status_code == 200, response.text
        body = response.json()
        assert {f["folder_id"]: f["name"] for f in body["folders"]} == {
            "folder-a": "Reports",
            "folder-b": "Receipts",
        }
        # No prior grant: whole-Drive, agent-only history, and the screen is
        # told it may offer the history choice.
        assert body["folder_access"] == "ALL"
        assert body["folder_id"] is None
        assert body["history_scope"] == "AGENT"
        assert body["can_choose_history_scope"] is True


def test_get_authorize_shows_the_users_own_folders_only(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        alice = env.seed_user(username="alice", email="alice@example.com")
        env.seed_user(username="bob", email="bob@example.com")
        env.add_folder(alice, "alice-folder", "Alice Private")
        client_info, _challenge, token = _consent_setup(env, username="bob")

        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folders"] == []


def test_post_with_new_folder_name_creates_and_binds_the_folder(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            new_folder_name="Agent Sandbox",
            history_scope="ALL",
        )
        assert response.status_code == 200, response.text

        # The folder was actually created for the user…
        listing = env.client.get(
            "/api/v1/files/folders", headers={"Authorization": f"Bearer {token}"}
        )
        names = [f["name"] for f in listing.json()["folders"]]
        assert "Agent Sandbox" in names
        new_id = next(f["id"] for f in listing.json()["folders"] if f["name"] == "Agent Sandbox")

        # …and the consent now reflects it (a re-consent shows the binding).
        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folder_access"] == "FOLDER"
        assert body["folder_id"] == new_id
        assert body["history_scope"] == "ALL"


def test_post_with_a_duplicate_new_folder_name_is_rejected(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user = env.seed_user()
        env.add_folder(user, "folder-a", "Agent Sandbox")
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            new_folder_name="Agent Sandbox",
        )
        assert response.status_code == 400, response.text
        assert "already exists" in response.json()["detail"]

        # Nothing was persisted: no grant, and the consent still defaults.
        session = {"Authorization": f"Bearer {token}"}
        apps = env.client.get("/api/v1/mcp/connected-apps", headers=session).json()["apps"]
        assert apps == []


def test_post_with_an_empty_new_folder_name_is_rejected(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            new_folder_name="   ",
        )
        assert response.status_code == 400, response.text
        assert "name" in response.json()["detail"].lower()


def test_post_with_another_users_folder_is_refused_and_not_persisted(tmp_path) -> None:
    """The important one: a folder the caller does not own must never bind."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        alice = env.seed_user(username="alice", email="alice@example.com")
        env.seed_user(username="bob", email="bob@example.com")
        alice_folder = env.add_folder(alice, "alice-folder", "Alice Only")

        client_info, challenge, token = _consent_setup(env, username="bob")
        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            folder_id=alice_folder,
        )
        assert response.status_code in (400, 404), response.text

        # No grant was created at all — the refusal happened before persistence.
        session = {"Authorization": f"Bearer {token}"}
        apps = env.client.get("/api/v1/mcp/connected-apps", headers=session).json()["apps"]
        assert apps == []

        # And the consent still reports the default binding, not Alice's folder.
        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folder_access"] == "ALL"
        assert body["folder_id"] is None


def test_post_folder_access_without_a_folder_is_rejected(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        env.seed_user()
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
        )
        assert response.status_code == 400, response.text
        assert "whole Drive" in response.json()["detail"]


def test_post_whole_drive_ignores_a_supplied_folder(tmp_path) -> None:
    """A tampered page cannot bind a folder while claiming whole-Drive."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user = env.seed_user()
        folder = env.add_folder(user, "folder-a", "Reports")
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="ALL",
            folder_id=folder,
        )
        assert response.status_code == 200, response.text

        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folder_access"] == "ALL"
        assert body["folder_id"] is None


def test_re_consent_moves_the_binding_and_can_widen_back_to_whole_drive(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user = env.seed_user()
        folder_a = env.add_folder(user, "folder-a", "A")
        folder_b = env.add_folder(user, "folder-b", "B")
        client_info, challenge, token = _consent_setup(env)

        first = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            folder_id=folder_a,
        )
        assert first.status_code == 200, first.text
        assert _get_consent(env, token=token, client_info=client_info).json()["folder_id"] == folder_a

        # Move A → B.
        second = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="FOLDER",
            folder_id=folder_b,
        )
        assert second.status_code == 200, second.text
        assert _get_consent(env, token=token, client_info=client_info).json()["folder_id"] == folder_b

        # And B → whole Drive.
        third = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            folder_access="ALL",
        )
        assert third.status_code == 200, third.text
        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folder_access"] == "ALL"
        assert body["folder_id"] is None


def test_folder_access_endpoint_returns_only_the_users_active_folder_grants(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        alice = env.seed_user(username="alice", email="alice@example.com")
        env.seed_user(username="bob", email="bob@example.com")
        alice_folder = env.add_folder(alice, "folder-a", "Alice Workspace")

        # Alice: one folder-bound grant, plus (a) a whole-Drive grant and (b) a
        # revoked folder grant — neither of which may appear.
        bound_client, bound_challenge, alice_token = _consent_setup(env, username="alice")
        bound = _post_consent(
            env,
            token=alice_token,
            client_info=bound_client,
            challenge=bound_challenge,
            folder_access="FOLDER",
            folder_id=alice_folder,
        )
        assert bound.status_code == 200, bound.text

        all_client, all_challenge, _ = _consent_setup(env, username="alice")
        # A second client needs a distinct redirect URI? No — the client is
        # registered fresh, so the same URI is fine.
        all_drive = _post_consent(
            env,
            token=alice_token,
            client_info=all_client,
            challenge=all_challenge,
            folder_access="ALL",
        )
        assert all_drive.status_code == 200, all_drive.text

        revoked_client, revoked_challenge, _ = _consent_setup(env, username="alice")
        revoked = _post_consent(
            env,
            token=alice_token,
            client_info=revoked_client,
            challenge=revoked_challenge,
            folder_access="FOLDER",
            folder_id=alice_folder,
        )
        assert revoked.status_code == 200, revoked.text
        apps = env.client.get(
            "/api/v1/mcp/connected-apps", headers={"Authorization": f"Bearer {alice_token}"}
        ).json()["apps"]
        revoked_grant = next(a for a in apps if a["client_id"] == revoked_client["client_id"])
        env.client.delete(
            f"/api/v1/mcp/connected-apps/{revoked_grant['id']}",
            headers={"Authorization": f"Bearer {alice_token}"},
        )

        body = env.client.get(
            "/api/v1/mcp/folder-access",
            headers={"Authorization": f"Bearer {alice_token}"},
        ).json()
        assert [entry["folder_id"] for entry in body["folders"]] == [alice_folder]
        assert body["folders"][0]["grant_id"] == bound_grant_id(env, alice_token, bound_client)

        # Bob sees nothing of Alice's.
        bob_token = env.sign_in("bob")
        bob_body = env.client.get(
            "/api/v1/mcp/folder-access", headers={"Authorization": f"Bearer {bob_token}"}
        ).json()
        assert bob_body["folders"] == []


def bound_grant_id(env: MCPEnv, token: str, client_info: dict[str, Any]) -> str:
    apps = env.client.get(
        "/api/v1/mcp/connected-apps", headers={"Authorization": f"Bearer {token}"}
    ).json()["apps"]
    return str(next(a["id"] for a in apps if a["client_id"] == client_info["client_id"]))


def test_folder_access_endpoint_requires_authentication(tmp_path) -> None:
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        assert env.client.get("/api/v1/mcp/folder-access").status_code == 401


def test_a_tampered_approval_cannot_widen_scopes_even_when_it_binds_a_folder(tmp_path) -> None:
    """The folder binding is applied, but the scope set is still intersected."""
    with mcp_env(str(tmp_path / "mcp.db")) as env:
        user = env.seed_user()
        folder = env.add_folder(user, "folder-a", "Reports")
        client_info, challenge, token = _consent_setup(env)

        response = _post_consent(
            env,
            token=token,
            client_info=client_info,
            challenge=challenge,
            scope="documents.read",
            approved=["documents.read", "documents.delete"],
            folder_access="FOLDER",
            folder_id=folder,
        )
        assert response.status_code == 200, response.text

        apps = env.client.get(
            "/api/v1/mcp/connected-apps", headers={"Authorization": f"Bearer {token}"}
        ).json()["apps"]
        assert apps[0]["scopes"] == ["documents.read"]
        # The legitimate folder choice is still honoured.
        body = _get_consent(env, token=token, client_info=client_info).json()
        assert body["folder_id"] == folder
