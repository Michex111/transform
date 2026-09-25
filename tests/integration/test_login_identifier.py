"""Sign-in identifier resolution: a username **or** an email address.

Sign-in used to match ``username`` only, so typing the address — which is what
the app itself shows as the account's identity (the shell prints it under the
display name, and registration asks for it) — answered "Incorrect username or
password" for a correct address with a correct password. Confirmed against the
running API before the change: the same account returned 200 for its username
and 401 for its email.

These tests drive real routers against a real (SQLite) database rather than a
stubbed repository, because the behaviour under test is *which row a query
returns*. The resolution rule turns on there being zero, one, or several
candidate rows, and a mock would happily return whatever the test assumed.

The ambiguity cases are not hypothetical: this project's own development
database holds ``michael`` and ``Michael`` as two separate accounts.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.infrastructure.config.settings import get_settings
from src.infrastructure.auth.jwt_provider import hash_password
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware

LOGIN = "/api/users/token"
ME = "/api/users/me"

PASSWORD = "Sup3rSecret!"
USERNAME = "ada"
EMAIL = "ada@example.com"


@dataclass
class LoginEnv:
    """A client plus the two things these tests need to do to the database."""

    client: TestClient
    db_path: str

    def seed(self, **overrides: Any) -> int:
        """Insert an account and return its id.

        Each call opens its own engine because the app's request sessions are
        short-lived and pooled separately; a shared in-memory database would not
        survive between them, which is why this is a file.
        """
        values: dict[str, Any] = {
            "username": USERNAME,
            "email": EMAIL,
            "hashed_password": hash_password(PASSWORD),
            "is_active": True,
            # Seeded verified so these tests do not depend on whether the
            # developer's `.env` configures an email transport (which decides
            # whether the sign-in gate is enforced). The gate has its own test.
            "email_verified": True,
        }
        values.update(overrides)
        return _run(_insert_user(self.db_path, values))

    def sign_in(self, identifier: str, password: str = PASSWORD) -> Response:
        return self.client.post(LOGIN, data={"username": identifier, "password": password})

    def user_id_for_token(self, response: Response) -> int:
        """Follow a successful sign-in to ``/users/me`` and read the account id."""
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        me = self.client.get(ME, headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, me.text
        return me.json()["id"]


def _run(coro):
    return asyncio.run(coro)


async def _insert_user(db_path: str, values: dict[str, Any]) -> int:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    try:
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with factory() as session:
            user = UserModel(**values)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user.id
    finally:
        await engine.dispose()


@contextmanager
def login_env(db_path: str) -> Generator[LoginEnv, None, None]:
    """A test client over SQLite storage.

    Mirrors ``test_email_verification_flow.py``: the app's startup migration step
    is stubbed out (the schema is created directly), and the database session is
    overridden. Nothing else is replaced, so the query under test is the real one.
    """

    async def no_op_initialize_database() -> None:
        return None

    async def prepare_schema() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    _run(prepare_schema())

    async def override_db():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    original_init = api_main.initialize_database
    original_is_allowed = RateLimitMiddleware._is_allowed

    async def allow_all(self, key, limit, window=60):
        del self, key, limit, window
        return True

    api_main.initialize_database = no_op_initialize_database
    # The sign-in endpoint sits in the strict auth bucket (10/min, per IP). This
    # file deliberately makes more attempts than that — several of them are
    # expected failures — so the limiter is disabled here. Its own behaviour is
    # covered by the rate-limit tests.
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db

    client = TestClient(api_main.app)
    try:
        yield LoginEnv(client=client, db_path=db_path)
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


@pytest.fixture
def env(tmp_path) -> Generator[LoginEnv, None, None]:
    with login_env(str(tmp_path / "login.db")) as test_env:
        yield test_env


@pytest.fixture
def enforced_email(monkeypatch):
    """Configure a transport so the unverified-sign-in gate is enforced.

    ``auto`` resolves to ``smtp`` from configuration alone. Nothing is sent —
    these tests never trigger a send.
    """
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://transform-web.onrender.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# The behaviour that was missing
# ---------------------------------------------------------------------------


def test_signs_in_with_the_username(env: LoginEnv) -> None:
    env.seed()
    assert env.sign_in(USERNAME).status_code == 200


def test_signs_in_with_the_email_address(env: LoginEnv) -> None:
    # The whole point: a correct address with a correct password used to be a 401.
    env.seed()
    assert env.sign_in(EMAIL).status_code == 200


def test_the_email_path_returns_the_matching_account(env: LoginEnv) -> None:
    # Not just "a 200" — the token must belong to the account that owns the
    # address, or a fix like this could hand out the wrong identity.
    env.seed()
    env.seed(username="grace", email="grace@example.com")
    ada = env.sign_in(EMAIL)
    assert env.user_id_for_token(ada) != env.user_id_for_token(env.sign_in("grace"))


def test_email_match_ignores_case(env: LoginEnv) -> None:
    # Addresses are case-insensitive in practice, and a phone keyboard produces
    # the capital far more easily than a desktop one.
    env.seed()
    assert env.sign_in("Ada@Example.COM").status_code == 200


def test_surrounding_whitespace_is_tolerated(env: LoginEnv) -> None:
    # Copy-pasting an address out of a mail client picks up a trailing space
    # surprisingly often, and that is not a wrong password.
    env.seed()
    assert env.sign_in("  ada@example.com  ").status_code == 200


# ---------------------------------------------------------------------------
# Username case handling, and the ambiguity guard
# ---------------------------------------------------------------------------


def test_username_falls_back_to_a_case_insensitive_match(env: LoginEnv) -> None:
    env.seed(username="Michael", email="michael@example.com")
    assert env.sign_in("michael").status_code == 200


def test_an_ambiguous_username_is_refused_rather_than_guessed(env: LoginEnv) -> None:
    # The important one. `michael` and `Michael` are two accounts; a
    # case-insensitive lookup with no exact match must NOT pick one, or the
    # change would sign a user into an account they did not name. Nobody is
    # locked out because both exact spellings still work — next test.
    env.seed(username="michael", email="lower@example.com")
    env.seed(username="Michael", email="upper@example.com")
    assert env.sign_in("MICHAEL").status_code == 401


def test_each_exact_spelling_still_reaches_its_own_account(env: LoginEnv) -> None:
    lower = env.seed(username="michael", email="lower@example.com")
    upper = env.seed(username="Michael", email="upper@example.com")

    assert env.user_id_for_token(env.sign_in("michael")) == lower
    assert env.user_id_for_token(env.sign_in("Michael")) == upper


def test_exact_username_wins_over_another_accounts_address(env: LoginEnv) -> None:
    # One account's address is another account's username. The exact-username
    # stage must stay FIRST so this resolves deterministically, exactly as it did
    # before the change, instead of depending on row order.
    by_username = env.seed(username="shared@example.com", email="first@example.com")
    env.seed(username="other", email="Shared@Example.com")

    assert env.user_id_for_token(env.sign_in("shared@example.com")) == by_username


# ---------------------------------------------------------------------------
# Nothing that protected the old path may have been lost
# ---------------------------------------------------------------------------


def test_a_wrong_password_is_refused_for_both_identifiers(env: LoginEnv) -> None:
    env.seed()
    assert env.sign_in(USERNAME, "wrong-password").status_code == 401
    assert env.sign_in(EMAIL, "wrong-password").status_code == 401


def test_an_unknown_identifier_is_refused_identically(env: LoginEnv) -> None:
    # Accepting the address must not make "an account exists with this address"
    # discoverable: an unknown address and an unknown username have to answer
    # exactly the same thing.
    env.seed()
    unknown_user = env.sign_in("nobody")
    unknown_email = env.sign_in("nobody@example.com")
    assert unknown_user.status_code == unknown_email.status_code == 401
    assert unknown_user.json() == unknown_email.json()


def test_the_unverified_gate_still_applies_when_signing_in_by_email(
    env: LoginEnv, enforced_email
) -> None:
    # The gate is checked after the password, so it has to survive the new lookup
    # path — otherwise signing in by address would be a way around it.
    env.seed(email_verified=False)
    response = env.sign_in(EMAIL)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


def test_an_inactive_account_cannot_obtain_a_token_by_either_identifier(env: LoginEnv) -> None:
    # Issuing a token to a deactivated account produced a half-signed-in state:
    # the SPA accepted the sign-in and navigated to the app, then every request
    # 401'd because `get_current_user` rejects inactive users. Refusing here is
    # the honest answer.
    env.seed(is_active=False)
    assert env.sign_in(USERNAME).status_code == 403
    assert env.sign_in(EMAIL).status_code == 403


def test_the_resolved_account_carries_its_names_into_the_response(
    env: LoginEnv,
) -> None:
    # End-to-end check that the new lookup feeds the same response builder, so
    # signing in by address still yields the name and initials the shell shows.
    env.seed(first_name="Ada", last_name="Lovelace")
    response = env.sign_in(EMAIL)
    assert response.status_code == 200
    me = env.client.get(
        ME, headers={"Authorization": f"Bearer {response.json()['access_token']}"}
    )
    body = me.json()
    assert body["display_name"] == "Ada Lovelace"
    assert body["initials"] == "AL"
