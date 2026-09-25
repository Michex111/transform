"""End-to-end tests for "forgot password" (reset by an emailed link).

Runs the real routers, real JWT auth and real SQL repositories against an
in-memory SQLite database, with only two things replaced:

* the database session (SQLite instead of PostgreSQL), and
* the email transport, so ``FakeEmailSender`` captures messages instead of
  sending them.

The token is recovered from the *rendered email body* rather than from
application state. That is deliberate: the plaintext token only exists in the
message, so any other way of getting it would mean the production code had kept
a copy it must not keep.

The properties under test are the ones that make the feature safe: the endpoint
must never reveal whether an address has an account, a link must work exactly
once, and completing a reset must let the (possibly never-verified) owner back
in.
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
from src.domain.security.enitities.one_time_token import hash_token
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import Base, get_db_session
from src.presentation.api.dependencies.service_dependencies import get_email_sender
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from tests.fakes.fake_email_sender import FakeEmailSender

REGISTER = "/api/users/register"
LOGIN = "/api/users/token"
VERIFY = "/api/users/verify-email"
FORGOT = "/api/users/forgot-password"
RESET = "/api/users/reset-password"

PASSWORD = "Sup3rSecret!"
NEW_PASSWORD = "EvenBetter9!"
OTHER_PASSWORD = "Attacker9!"
EMAIL = "ada@example.com"
USERNAME = "ada"

#: The frozen contract's exact response body. Asserted literally so a future
#: edit to the copy is a deliberate, reviewed change rather than a drift.
ACCEPTED_MESSAGE = (
    "If an account with that email address exists, we've sent instructions for "
    "resetting your password."
)

#: One message for "unknown", "already used" and "expired" alike.
INVALID_MESSAGE = (
    "This password reset link is invalid or has expired. Request a new one and "
    "try again."
)

UPDATED_MESSAGE = "Your password has been updated. Sign in with your new password."


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@contextmanager
def password_reset_client(
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
    # The reset endpoints deliberately sit in the strict auth bucket (10/min).
    # These tests make more calls than that on purpose, so the limiter is
    # disabled here; its own behaviour is covered by
    # test_rate_limit_resolution.py.
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
    nothing leaves the process. The gate matters here because one of the
    properties under test is that a completed reset lets an account that never
    verified sign in.
    """
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("APP_BASE_URL", "https://transform-web.onrender.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def register(client: TestClient, *, username: str = USERNAME, email: str = EMAIL, password: str = PASSWORD):
    return client.post(
        REGISTER, json={"username": username, "email": email, "password": password}
    )


def login(client: TestClient, *, username: str = USERNAME, password: str = PASSWORD):
    return client.post(LOGIN, data={"username": username, "password": password})


def forgot(client: TestClient, *, email: str = EMAIL):
    return client.post(FORGOT, json={"email": email})


def reset(client: TestClient, *, token: str, new_password: str = NEW_PASSWORD):
    return client.post(RESET, json={"token": token, "new_password": new_password})


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


def _mutate_user(db_path: str, mutate) -> None:
    """Apply ``mutate`` to the stored row (used to reach edge cases without sleeping)."""

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = (
                    await session.execute(select(UserModel).where(UserModel.username == USERNAME))
                ).scalars().one()
                mutate(user)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def expire_reset_token(db_path: str) -> None:
    """Backdate the stored reset token so the expiry branch is reachable."""

    def _mutate(user: UserModel) -> None:
        user.password_reset_expires_at = datetime.now(UTC) - timedelta(minutes=1)

    _mutate_user(db_path, _mutate)


def age_the_reset_cooldown(db_path: str) -> None:
    """Backdate the last-sent timestamp past the resend cooldown.

    Preferred over setting the cooldown to zero, so the throttle itself stays
    configured exactly as it is in production while tests that are about the
    *new link* are not blocked by it.
    """

    def _mutate(user: UserModel) -> None:
        user.password_reset_sent_at = datetime.now(UTC) - timedelta(hours=1)

    _mutate_user(db_path, _mutate)


def deactivate(db_path: str) -> None:
    def _mutate(user: UserModel) -> None:
        user.is_active = False

    _mutate_user(db_path, _mutate)


def reset_emails(sender: FakeEmailSender) -> list:
    """Only the reset messages, so registration's verification email is ignored."""
    return [m for m in sender.sent if "reset-password?token=" in m.text_body]


# ---------------------------------------------------------------------------
# Requesting a link
# ---------------------------------------------------------------------------


def test_an_unknown_address_is_indistinguishable_from_a_known_one(
    tmp_path, enforced_email
) -> None:
    """Otherwise this unauthenticated endpoint enumerates registered addresses."""
    sender = FakeEmailSender()
    with password_reset_client(str(tmp_path / "a.db"), email_sender=sender) as client:
        register(client)
        known = forgot(client)
        unknown = forgot(client, email="nobody@example.com")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert known.json() == {"message": ACCEPTED_MESSAGE}


def test_the_token_is_recoverable_from_the_rendered_email(tmp_path, enforced_email) -> None:
    """The plaintext token exists only in the message — nowhere else may hold it."""
    sender = FakeEmailSender()
    with password_reset_client(str(tmp_path / "b.db"), email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        message = sender.last

    assert token
    assert f"reset-password?token={token}" in message.text_body
    assert message.subject == "Reset your Transform password"
    assert "Choose a new password" in message.html_body


def test_the_raw_token_is_never_stored(tmp_path, enforced_email) -> None:
    """A database leak must not hand over full account takeover."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "c.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        stored = read_user(db_path)

    assert stored.password_reset_token_hash is not None
    assert stored.password_reset_token_hash == hash_token(token)
    assert stored.password_reset_token_hash != token
    assert token not in stored.password_reset_token_hash
    assert len(stored.password_reset_token_hash) == 64


def test_a_deactivated_account_is_emailed_nothing_but_still_accepted(
    tmp_path, enforced_email
) -> None:
    """An operator disabled it on purpose, so no recovery link may be sent."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "d.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        deactivate(db_path)
        response = forgot(client)

    assert response.status_code == 202, response.text
    assert response.json() == {"message": ACCEPTED_MESSAGE}
    assert reset_emails(sender) == []


def test_the_address_match_is_case_insensitive(tmp_path, enforced_email) -> None:
    """Registration stores the address as typed; recovery must still find it."""
    sender = FakeEmailSender()
    with password_reset_client(str(tmp_path / "e.db"), email_sender=sender) as client:
        register(client)
        response = forgot(client, email="ADA@example.com")

    assert response.status_code == 202, response.text
    assert [m.to for m in reset_emails(sender)] == [EMAIL]


def test_the_resend_cooldown_suppresses_a_second_email(tmp_path, enforced_email) -> None:
    """Without it the endpoint is a free mail-bomb aimed at a third party."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "f.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        first = forgot(client)
        assert len(reset_emails(sender)) == 1
        second = forgot(client)
        suppressed = len(reset_emails(sender))
        # Backdating the timestamp lets one through, which also pins that the
        # throttle — not something else — was doing the suppressing.
        age_the_reset_cooldown(db_path)
        third = forgot(client)

    assert first.status_code == second.status_code == third.status_code == 202
    assert suppressed == 1, "a second email was sent inside the cooldown"
    assert len(reset_emails(sender)) == 2


def test_a_reset_request_does_not_disturb_an_outstanding_verification_token(
    tmp_path, enforced_email
) -> None:
    """The two flows share an account but not a token: neither may clobber the other."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "g.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        verification_token = sender.verification_token_for(EMAIL)
        forgot(client)
        stored = read_user(db_path)
        # The verification link still works afterwards.
        verified = verify(client, verification_token)

    assert stored.email_verification_token_hash == hash_token(verification_token)
    assert stored.email_verification_token_hash is not None
    assert verified.status_code == 200, verified.text


# ---------------------------------------------------------------------------
# Consuming a link
# ---------------------------------------------------------------------------


def test_a_valid_token_sets_the_new_password(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    db_path = str(tmp_path / "h.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        response = reset(client, token=sender.password_reset_token_for(EMAIL))
        # The old password must stop working...
        old = login(client)
        # ...and the new one must work.
        new = login(client, password=NEW_PASSWORD)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "username": USERNAME,
        "message": UPDATED_MESSAGE,
    }
    assert old.status_code == 401
    assert new.status_code == 200, new.text
    assert new.json()["access_token"]


def test_a_reset_clears_the_stored_token(tmp_path, enforced_email) -> None:
    """Clearing the hash is what makes the link single-use."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "i.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        reset(client, token=token)
        stored = read_user(db_path)

    assert stored.password_reset_token_hash is None
    assert stored.password_reset_expires_at is None
    # The plaintext token is nowhere on the row, before or after.
    assert token != stored.hashed_password
    assert token not in stored.hashed_password


def test_a_reset_token_cannot_be_used_twice(tmp_path, enforced_email) -> None:
    """A replayed link must fail and must not change the password again."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "j.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        first = reset(client, token=token)
        after_first = read_user(db_path).hashed_password
        second = reset(client, token=token, new_password=OTHER_PASSWORD)
        after_second = read_user(db_path).hashed_password
        still_new = login(client, password=NEW_PASSWORD)
        attacker = login(client, password=OTHER_PASSWORD)

    assert first.status_code == 200, first.text
    assert second.status_code == 400, second.text
    assert second.json()["detail"] == INVALID_MESSAGE
    assert after_second == after_first
    assert still_new.status_code == 200
    assert attacker.status_code == 401


def test_an_expired_token_is_rejected(tmp_path, enforced_email) -> None:
    sender = FakeEmailSender()
    db_path = str(tmp_path / "k.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        before = read_user(db_path).hashed_password
        expire_reset_token(db_path)
        response = reset(client, token=token)
        stored = read_user(db_path)

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == INVALID_MESSAGE
    assert stored.hashed_password == before
    # The dead token is cleared so the row reflects reality.
    assert stored.password_reset_token_hash is None


def test_a_forged_token_is_rejected(tmp_path, enforced_email) -> None:
    with password_reset_client(str(tmp_path / "l.db")) as client:
        register(client)
        response = reset(client, token="not-a-real-token")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == INVALID_MESSAGE


@pytest.mark.parametrize("short_password", ["short7!", "1234567"])
def test_a_too_short_password_is_rejected_by_schema_validation(
    tmp_path, enforced_email, short_password: str
) -> None:
    """7 characters, one below the account policy — a 422, not a consumed link."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "m.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        forgot(client)
        token = sender.password_reset_token_for(EMAIL)
        response = reset(client, token=token, new_password=short_password)
        stored = read_user(db_path)

    assert response.status_code == 422
    # The link is untouched, so the user can simply choose a longer password.
    assert stored.password_reset_token_hash is not None
    assert stored.password_reset_token_hash == hash_token(token)


def test_a_missing_field_is_rejected_by_schema_validation(tmp_path, enforced_email) -> None:
    with password_reset_client(str(tmp_path / "n.db")) as client:
        assert client.post(RESET, json={"token": "abc"}).status_code == 422
        assert client.post(RESET, json={"new_password": NEW_PASSWORD}).status_code == 422


# ---------------------------------------------------------------------------
# Verification interaction
# ---------------------------------------------------------------------------


def test_an_unverified_account_becomes_verified_by_completing_a_reset(
    tmp_path, enforced_email
) -> None:
    """Receiving the link proves the mailbox — exactly what verification proves.

    Without this, a user who never clicked the original verification link and
    forgot their password would reset it and still be refused at sign-in, with
    no self-service way out.
    """
    sender = FakeEmailSender()
    db_path = str(tmp_path / "o.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        # The verification gate refuses sign-in before the reset...
        assert login(client).status_code == 403
        forgot(client)
        reset(client, token=sender.password_reset_token_for(EMAIL))
        stored = read_user(db_path)
        # ...and the new password works afterwards, with no separate verify step.
        signed_in = login(client, password=NEW_PASSWORD)

    assert stored.email_verified is True
    assert stored.email_verified_at is not None
    assert signed_in.status_code == 200, signed_in.text


def test_a_reset_does_not_overwrite_an_existing_verification_timestamp(
    tmp_path, enforced_email
) -> None:
    """`coalesce` keeps the original proof time — the audit trail stays honest."""
    sender = FakeEmailSender()
    db_path = str(tmp_path / "p.db")
    with password_reset_client(db_path, email_sender=sender) as client:
        register(client)
        verify(client, sender.verification_token_for(EMAIL))
        original = read_user(db_path).email_verified_at
        forgot(client)
        reset(client, token=sender.password_reset_token_for(EMAIL))
        stored = read_user(db_path)

    assert stored.email_verified is True
    assert stored.email_verified_at == original
