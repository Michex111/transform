"""End-to-end tests for sign-up email verification.

Runs the real routers, real JWT auth and real SQL repositories against an
in-memory SQLite database, with only two things replaced:

* the database session (SQLite instead of PostgreSQL), and
* the email transport, so ``FakeEmailSender`` captures messages instead of
  sending them.

The token is recovered from the *rendered email body* rather than from
application state. That is deliberate: the plaintext token only exists in the
message, so any other way of getting it would mean the production code had kept
a copy it must not keep.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.service_dependencies import get_email_sender
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from tests.fakes.fake_email_sender import FakeEmailSender

REGISTER = "/api/users/register"
LOGIN = "/api/users/token"
VERIFY = "/api/users/verify-email"
RESEND = "/api/users/resend-verification"

PASSWORD = "Sup3rSecret!"
EMAIL = "ada@example.com"
USERNAME = "ada"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@contextmanager
def verification_client(
    db_path: str,
    *,
    email_sender: FakeEmailSender | None = None,
) -> Generator[TestClient, None, None]:
    """A test client with SQLite storage and a capturing email transport."""
    sender = email_sender or FakeEmailSender()

    async def no_op_initialize_database() -> None:
        return None

    async def prepare_schema() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(prepare_schema())

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
    # The verification endpoints deliberately sit in the strict auth bucket
    # (10/min). These tests make more calls than that on purpose, so the limiter
    # is disabled here; its own behaviour is covered by
    # test_rate_limit_resolution.py and the auth-bucket test below.
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_email_sender] = lambda: sender

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


@pytest.fixture
def enforced_email(monkeypatch):
    """Configure a real transport so the sign-in gate is enforced.

    ``auto`` resolves to ``smtp`` instead of ``console`` purely from
    configuration; the sends themselves are captured by the fake sender, so
    nothing leaves the process.
    """
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://transform-web.onrender.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def suspended_email(monkeypatch):
    """No transport configured, so the gate must stay open."""
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def register(client: TestClient, *, username: str = USERNAME, email: str = EMAIL, password: str = PASSWORD):
    return client.post(
        REGISTER, json={"username": username, "email": email, "password": password}
    )


def login(client: TestClient, *, username: str = USERNAME, password: str = PASSWORD):
    return client.post(LOGIN, data={"username": username, "password": password})


def verify(client: TestClient, token: str):
    return client.post(VERIFY, json={"token": token})


def read_user(db_path: str, username: str = USERNAME) -> UserModel:
    """Read the persisted row to assert on stored state, not just responses."""

    async def _run() -> UserModel:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                result = await session.execute(
                    select(UserModel).where(UserModel.username == username)
                )
                return result.scalars().one()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def expire_token(db_path: str) -> None:
    """Backdate the stored token so the expiry branch is reachable without sleeping."""

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = (
                    await session.execute(select(UserModel).where(UserModel.username == USERNAME))
                ).scalars().one()
                user.email_verification_expires_at = datetime.now(UTC) - timedelta(minutes=1)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def age_the_resend_cooldown(db_path: str) -> None:
    """Backdate the last-sent timestamp past the cooldown.

    Preferred over setting the cooldown to zero, so the throttle itself stays
    configured exactly as it is in production while tests that are about the
    *new link* are not blocked by it.
    """

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = (
                    await session.execute(select(UserModel).where(UserModel.username == USERNAME))
                ).scalars().one()
                user.email_verification_sent_at = datetime.now(UTC) - timedelta(hours=1)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_registration_creates_an_unverified_account(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "a.db")) as client:
        response = register(client)
        payload = response.json()
        stored = read_user(str(tmp_path / "a.db"))

    assert response.status_code == 201, response.text
    assert payload["email_verified"] is False
    assert stored.email_verified is False


def test_registration_sends_a_verification_email(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "b.db"), email_sender=sender) as client:
        register(client)

    assert len(sender.sent) == 1
    message = sender.last
    assert message.to == EMAIL
    assert "verify" in message.subject.lower()
    # The link must point at the SPA route that handles it, not at the API.
    assert "https://transform-web.onrender.com/verify-email?token=" in message.text_body


def test_the_raw_token_is_never_stored(tmp_path, enforced_email) -> None:
    """A database leak must not hand over the ability to verify accounts."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "c.db")
    with verification_client(db_path, email_sender=sender) as client:
        register(client)

    token = sender.verification_token_for(EMAIL)
    stored = read_user(db_path)

    assert stored.email_verification_token_hash is not None
    assert stored.email_verification_token_hash != token
    assert token not in stored.email_verification_token_hash
    assert len(stored.email_verification_token_hash) == 64


def test_registration_still_succeeds_when_the_email_cannot_be_sent(
    tmp_path, enforced_email
) -> None:
    """The account exists; a 500 here would be unrecoverable for the user.

    The row is already committed by the time the send happens, so failing the
    request would leave them unable to retry the signup (409) or see why.
    """
    sender = FakeEmailSender()
    sender.failure = RuntimeError("smtp is down")
    db_path = str(tmp_path / "d.db")

    with verification_client(db_path, email_sender=sender) as client:
        response = register(client)
        stored = read_user(db_path)

    assert response.status_code == 201, response.text
    assert stored.email_verified is False
    # `sent_at` stays unset so the resend path is not throttled by a message
    # that never went out.
    assert stored.email_verification_sent_at is None


def test_registration_rejects_a_duplicate_username_or_email(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "e.db")) as client:
        assert register(client).status_code == 201
        duplicate = register(client)
        other_email_same_username = register(client, email="other@example.com")

    assert duplicate.status_code == 409
    assert other_email_same_username.status_code == 409


# ---------------------------------------------------------------------------
# The sign-in gate
# ---------------------------------------------------------------------------


def test_sign_in_is_refused_while_the_email_is_unverified(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "f.db")) as client:
        register(client)
        response = login(client)

    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    # A machine-readable code lets the SPA offer "resend" instead of showing a
    # generic credential error and discarding the (correct) credentials.
    assert detail["code"] == "EMAIL_NOT_VERIFIED"
    assert detail["message"]


def test_sign_in_is_not_refused_for_a_wrong_password(tmp_path, enforced_email) -> None:
    """401 must win over 403, or the gate becomes a username oracle."""
    with verification_client(str(tmp_path / "g.db")) as client:
        register(client)
        response = login(client, password="WrongPassword1!")

    assert response.status_code == 401


def test_wrong_password_and_unknown_username_are_both_401(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "h.db")) as client:
        register(client)
        unknown = login(client, username="nobody")

    assert unknown.status_code == 401


def test_sign_in_is_allowed_once_the_email_is_verified(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "i.db"), email_sender=sender) as client:
        register(client)
        verify(client, sender.verification_token_for(EMAIL))
        response = login(client)

    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


def test_the_gate_is_suspended_when_no_transport_can_deliver(tmp_path, suspended_email) -> None:
    """Fail open, not closed.

    If no transport is configured the link can never arrive, so enforcing the
    gate would lock out every new signup with no self-service way out. The
    account is still created unverified, and enforcement switches on by itself
    once a transport is configured.
    """
    with verification_client(str(tmp_path / "j.db")) as client:
        register(client)
        response = login(client)
        payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["access_token"]


def test_verification_requirement_can_be_disabled(tmp_path, enforced_email, monkeypatch) -> None:
    """The operational escape hatch: a working transport, but the gate is off."""
    monkeypatch.setenv("EMAIL_VERIFICATION_REQUIRED", "false")
    get_settings.cache_clear()

    with verification_client(str(tmp_path / "k.db")) as client:
        register(client)
        response = login(client)

    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


def test_verifying_activates_the_account(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    db_path = str(tmp_path / "l.db")
    with verification_client(db_path, email_sender=sender) as client:
        register(client)
        response = verify(client, sender.verification_token_for(EMAIL))
        stored = read_user(db_path)

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["already_verified"] is False
    # Returned so the sign-in form can be pre-filled with the right username.
    assert response.json()["username"] == USERNAME
    assert stored.email_verified is True
    assert stored.email_verified_at is not None
    # The token is cleared, which is what makes it single-use.
    assert stored.email_verification_token_hash is None


def test_a_verification_token_cannot_be_used_twice(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "m.db"), email_sender=sender) as client:
        register(client)
        token = sender.verification_token_for(EMAIL)
        first = verify(client, token)
        second = verify(client, token)

    assert first.status_code == 200
    assert second.status_code == 400, second.text


def test_an_expired_token_is_rejected(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    db_path = str(tmp_path / "n.db")
    with verification_client(db_path, email_sender=sender) as client:
        register(client)
        token = sender.verification_token_for(EMAIL)
        expire_token(db_path)
        response = verify(client, token)
        stored = read_user(db_path)

    assert response.status_code == 400, response.text
    assert stored.email_verified is False
    # The dead token is cleared so the row reflects reality.
    assert stored.email_verification_token_hash is None


def test_a_forged_token_is_rejected(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "o.db")) as client:
        register(client)
        response = verify(client, "not-a-real-token")

    assert response.status_code == 400
    # No hint about whether the token was close, well-formed, or unknown.
    assert "invalid" in response.json()["detail"].lower()


def test_an_empty_token_is_rejected_by_schema_validation(tmp_path, enforced_email) -> None:
    with verification_client(str(tmp_path / "p.db")) as client:
        response = client.post(VERIFY, json={"token": ""})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Resending
# ---------------------------------------------------------------------------


def test_resend_issues_a_link_that_works(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    db_path = str(tmp_path / "q.db")
    with verification_client(db_path, email_sender=sender) as client:
        register(client)
        first = sender.verification_token_for(EMAIL)
        # Only age the throttle: the behaviour under test is the new link, and
        # the throttle itself is covered by its own test below.
        age_the_resend_cooldown(db_path)
        response = client.post(RESEND, json={"email": EMAIL})
        second = sender.verification_token_for(EMAIL)
        verified = verify(client, second)
        stale = verify(client, first)

    assert response.status_code == 202, response.text
    assert second != first
    assert verified.status_code == 200, verified.text
    # Requesting a new link invalidates the previous one.
    assert stale.status_code == 400


def test_resend_is_suppressed_inside_the_cooldown(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "r.db"), email_sender=sender) as client:
        register(client)
        assert len(sender.sent) == 1
        response = client.post(RESEND, json={"email": EMAIL})

    assert response.status_code == 202, response.text
    assert len(sender.sent) == 1, "a second email was sent inside the cooldown"


def test_resend_for_an_unknown_address_is_indistinguishable(tmp_path, enforced_email) -> None:
    """Otherwise this endpoint enumerates registered addresses."""
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "s.db"), email_sender=sender) as client:
        register(client)
        known_unverified = client.post(RESEND, json={"email": EMAIL})
        unknown = client.post(RESEND, json={"email": "nobody@example.com"})

    assert known_unverified.status_code == unknown.status_code == 202
    assert known_unverified.json() == unknown.json()
    assert not any(m.to == "nobody@example.com" for m in sender.sent)


def test_resend_for_an_already_verified_account_is_indistinguishable(
    tmp_path, enforced_email
) -> None:
    sender = FakeEmailSender()
    with verification_client(str(tmp_path / "t.db"), email_sender=sender) as client:
        register(client)
        verify(client, sender.verification_token_for(EMAIL))
        sent_before = len(sender.sent)
        response = client.post(RESEND, json={"email": EMAIL})

    assert response.status_code == 202, response.text
    assert len(sender.sent) == sent_before, "an already-verified account was emailed"


def test_resend_is_case_insensitive_on_the_address(tmp_path, enforced_email) -> None:
    """Registration stores the address as typed; recovery must still find it."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "u.db")
    with verification_client(db_path, email_sender=sender) as client:
        register(client, email="Ada@Example.COM")
        age_the_resend_cooldown(db_path)
        response = client.post(RESEND, json={"email": "ada@example.com"})

    assert response.status_code == 202, response.text
    assert any(m.to == "Ada@Example.COM" for m in sender.sent)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_the_verification_endpoints_use_the_strict_auth_bucket() -> None:
    """They are unauthenticated and act on a secret / send to a third party.

    Leaving them on the looser per-IP default would make resend a cheap
    mail-bomb and leave the token endpoint open to high-rate guessing.
    """
    from src.presentation.api.middleware.rate_limit import _AUTH_PATHS

    assert VERIFY in _AUTH_PATHS
    assert RESEND in _AUTH_PATHS
