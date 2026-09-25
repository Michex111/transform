"""End-to-end tests for phone verification.

Runs the real routers, real JWT auth and real SQL repositories against an
in-memory SQLite database, with only three things replaced:

* the database session (SQLite instead of PostgreSQL),
* the email transport (so registration never touches the network), and
* the SMS transport, so ``FakeSmsSender`` captures messages instead of sending
  them.

The code is recovered from the *rendered SMS body* rather than from application
state. That is deliberate: the plaintext code only exists in the message, so any
other way of getting it would mean the production code had kept a copy it must
not keep.
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
from src.presentation.api.dependencies.service_dependencies import (
    get_email_sender,
    get_sms_sender,
)
from src.presentation.api.middleware.rate_limit import RateLimitMiddleware
from tests.fakes.fake_email_sender import FakeEmailSender
from tests.fakes.fake_sms_sender import FakeSmsSender

REGISTER = "/api/users/register"
LOGIN = "/api/users/token"
REQUEST_PHONE = "/api/users/me/phone"
RESEND_PHONE = "/api/users/me/phone/resend"
VERIFY_PHONE = "/api/users/me/phone/verify"
DELETE_PHONE = "/api/users/me/phone"

PASSWORD = "Sup3rSecret!"
PHONE = "+14155552671"
OTHER_PHONE = "+442079460958"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@contextmanager
def phone_client(
    db_path: str,
    *,
    sms_sender: FakeSmsSender | None = None,
) -> Generator[TestClient, None, None]:
    """A test client with SQLite storage and a capturing SMS transport."""
    sender = sms_sender or FakeSmsSender()

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
    # The phone endpoints deliberately sit in the strict auth bucket (10/min),
    # and a 10-request budget is exactly the kind of limit these tests must
    # exceed on purpose. The limiter's own behaviour is covered by
    # test_rate_limit_resolution.py.
    RateLimitMiddleware._is_allowed = allow_all
    api_main.app.dependency_overrides[get_db_session] = override_db
    api_main.app.dependency_overrides[get_email_sender] = lambda: FakeEmailSender()
    api_main.app.dependency_overrides[get_sms_sender] = lambda: sender

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init
        RateLimitMiddleware._is_allowed = original_is_allowed


@pytest.fixture
def sign_in_without_verifying_email(monkeypatch):
    """Keep the email gate open so these tests can sign in.

    Phone verification has nothing to do with the email gate, and the local
    ``.env`` may point ``auto`` at a real transport (Resend/SMTP), which would
    enforce it. Pinning the email transport to console suspends the gate without
    touching anything the phone flow reads.
    """
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def register(
    client: TestClient,
    *,
    username: str = "ada",
    email: str = "ada@example.com",
    password: str = PASSWORD,
) -> None:
    response = client.post(
        REGISTER, json={"username": username, "email": email, "password": password}
    )
    assert response.status_code == 201, response.text


def auth_headers(
    client: TestClient,
    *,
    username: str = "ada",
    password: str = PASSWORD,
) -> dict[str, str]:
    response = client.post(LOGIN, data={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def request_phone(client: TestClient, headers: dict[str, str], number: str = PHONE):
    return client.post(REQUEST_PHONE, json={"phone_number": number}, headers=headers)


def verify_phone(client: TestClient, headers: dict[str, str], code: str):
    return client.post(VERIFY_PHONE, json={"code": code}, headers=headers)


def read_user(db_path: str, username: str = "ada") -> UserModel:
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


def mutate_user(db_path: str, username: str, **values) -> None:
    """Patch stored bookkeeping columns so time-based branches are reachable."""

    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = (
                    await session.execute(
                        select(UserModel).where(UserModel.username == username)
                    )
                ).scalars().one()
                for key, value in values.items():
                    setattr(user, key, value)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Requesting a code
# ---------------------------------------------------------------------------


def test_requesting_a_code_answers_202_and_sends_an_sms(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "a.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        response = request_phone(client, headers)
        stored = read_user(db_path)

    assert response.status_code == 202, response.text
    assert len(sender.sent) == 1
    assert sender.last.to == PHONE
    # Delivery accepted, not delivery confirmed: the code is live for the TTL.
    assert response.json()["phone_number"] == PHONE
    assert response.json()["phone_verified"] is False
    assert response.json()["expires_in_seconds"] == 10 * 60
    # The number is stored immediately, verified or not, so a reload shows it.
    assert stored.phone_number == PHONE
    assert stored.phone_verified is False


def test_the_raw_code_is_never_stored(tmp_path, sign_in_without_verifying_email) -> None:
    """A database leak must not hand over the ability to verify accounts."""
    sender = FakeSmsSender()
    db_path = str(tmp_path / "b.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)

    code = sender.code_for(PHONE)
    stored = read_user(db_path)

    assert stored.phone_verification_code_hash is not None
    assert stored.phone_verification_code_hash != code
    assert code not in stored.phone_verification_code_hash
    assert len(stored.phone_verification_code_hash) == 64


def test_an_invalid_number_is_rejected_with_a_machine_readable_code(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "c.db"), sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        # Long enough to pass the schema, but not E.164 — the domain rule, not
        # the length bound, is what has to produce this 400.
        response = request_phone(client, headers, number="4155552671")

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_PHONE_NUMBER"
    assert response.json()["detail"]["message"]
    # Nothing was sent, and nothing was stored.
    assert sender.sent == []


def test_a_number_too_short_for_the_schema_is_a_422(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """The declared bounds reject it before the domain rule is reached."""
    with phone_client(str(tmp_path / "c2.db")) as client:
        register(client)
        headers = auth_headers(client)
        response = request_phone(client, headers, number="12345")

    assert response.status_code == 422


def test_a_number_already_verified_by_another_account_is_a_conflict(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """409 rather than a unique-index IntegrityError surfacing as a 500."""
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "d.db"), sms_sender=sender) as client:
        register(client, username="ada", email="ada@example.com")
        first_headers = auth_headers(client)
        request_phone(client, first_headers)
        verify_phone(client, first_headers, sender.code_for(PHONE))

        register(client, username="bob", email="bob@example.com")
        second_headers = auth_headers(client, username="bob")
        response = request_phone(client, second_headers)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PHONE_IN_USE"


def test_a_number_held_unverified_by_another_account_is_also_a_conflict(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """The unique index cannot distinguish verified from not, so neither can we.

    Silently taking the number over would leave two accounts pointing at one
    phone — and would surface to the first user as a mysterious 500.
    """
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "e.db"), sms_sender=sender) as client:
        register(client, username="ada", email="ada@example.com")
        request_phone(client, auth_headers(client))  # requested, never verified

        register(client, username="bob", email="bob@example.com")
        response = request_phone(client, auth_headers(client, username="bob"))

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PHONE_IN_USE"


def test_the_cooldown_suppresses_a_second_send_silently(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """Still a 202, nothing sent — otherwise this is a probe and an SMS bomb."""
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "f.db"), sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        response = request_phone(client, headers)

    assert response.status_code == 202, response.text
    assert len(sender.sent) == 1  # only the first request sent anything
    # The client needs the remaining time or its button looks broken.
    assert response.json()["resend_available_in_seconds"] is not None
    assert response.json()["resend_available_in_seconds"] > 0


def test_a_send_after_the_cooldown_issues_a_new_working_code(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "g.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        first = sender.code_for(PHONE)

        # Only age the throttle: the behaviour under test is the new code.
        mutate_user(
            db_path,
            "ada",
            phone_verification_sent_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        response = request_phone(client, headers)
        second = sender.code_for(PHONE)

        # The new code works; the superseded one does not.
        new_is_valid = verify_phone(client, headers, second)

    assert response.status_code == 202, response.text
    assert len(sender.sent) == 2
    assert second != first
    assert new_is_valid.status_code == 200, new_is_valid.text


def test_a_delivery_failure_is_reported_as_503_not_a_silent_success(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """The user is waiting for a code; a 202 with nothing sent would be a lie."""
    sender = FakeSmsSender()
    sender.failure = RuntimeError("twilio rejected the message (HTTP 400)")
    db_path = str(tmp_path / "h.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        response = request_phone(client, headers)
        stored = read_user(db_path)

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "SMS_DELIVERY_FAILED"
    # The internal failure is not echoed to the client...
    assert "twilio" not in response.text.lower()
    # ...the code is kept so a retry can reuse the stored state...
    assert stored.phone_verification_code_hash is not None
    # ...and the cooldown is *not* started, so the retry is not throttled by a
    # message that never went out.
    assert stored.phone_verification_sent_at is None


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


def test_verifying_the_captured_code_marks_the_number_verified(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "i.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        response = verify_phone(client, headers, sender.code_for(PHONE))
        stored = read_user(db_path)

    assert response.status_code == 200, response.text
    assert response.json()["phone_verified"] is True
    assert response.json()["phone_number"] == PHONE
    assert stored.phone_verified is True
    assert stored.phone_verified_at is not None
    # The digest is cleared, which is what makes the code single-use.
    assert stored.phone_verification_code_hash is None


def test_a_verified_code_cannot_be_replayed(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "j.db"), sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        code = sender.code_for(PHONE)
        first = verify_phone(client, headers, code)
        second = verify_phone(client, headers, code)

    assert first.status_code == 200
    assert second.status_code == 400, second.text
    assert second.json()["detail"]["code"] == "PHONE_CODE_EXPIRED"


def test_a_wrong_code_is_rejected_and_counted(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "k.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        captured = sender.code_for(PHONE)
        wrong = "000000" if captured != "000000" else "111111"

        response = verify_phone(client, headers, wrong)
        stored = read_user(db_path)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "PHONE_CODE_INVALID"
    # One guess spent, and the code is still live.
    assert stored.phone_verification_attempts == 1
    assert stored.phone_verification_code_hash is not None
    assert stored.phone_verified is False


def test_exhausting_the_attempt_budget_locks_the_code(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """The counter is the only thing between a 6-digit code and online guessing."""
    sender = FakeSmsSender()
    db_path = str(tmp_path / "l.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        captured = sender.code_for(PHONE)

        wrong_codes = [c for c in ("000001", "000002", "000003", "000004", "000005", "000006") if c != captured]
        statuses = [verify_phone(client, headers, code).status_code for code in wrong_codes[:5]]
        # The sixth attempt happens with the budget already spent.
        final = verify_phone(client, headers, wrong_codes[5])
        # Even the *correct* code is refused once the budget is gone: the code
        # is dead, and only a new one can be used.
        correct_after = verify_phone(client, headers, captured)

    assert statuses == [400, 400, 400, 400, 400]
    assert final.status_code == 429, final.text
    assert final.json()["detail"]["code"] == "PHONE_CODE_ATTEMPTS_EXCEEDED"
    assert correct_after.status_code == 429


def test_an_expired_code_is_rejected_as_expired(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "m.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        captured = sender.code_for(PHONE)

        mutate_user(
            db_path,
            "ada",
            phone_verification_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        response = verify_phone(client, headers, captured)

    assert response.status_code == 400, response.text
    # Expired, not "wrong": requesting a new code is the actionable advice.
    assert response.json()["detail"]["code"] == "PHONE_CODE_EXPIRED"


def test_verifying_without_requesting_a_code_is_rejected(
    tmp_path, sign_in_without_verifying_email
) -> None:
    with phone_client(str(tmp_path / "n.db")) as client:
        register(client)
        headers = auth_headers(client)
        response = verify_phone(client, headers, "123456")

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PHONE_CODE_EXPIRED"


def test_a_code_for_one_account_does_not_verify_another(
    tmp_path, sign_in_without_verifying_email
) -> None:
    """The digest is bound to the user id, so the code cannot be replayed across accounts."""
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "o.db"), sms_sender=sender) as client:
        register(client, username="ada", email="ada@example.com")
        ada_headers = auth_headers(client)
        request_phone(client, ada_headers)
        ada_code = sender.code_for(PHONE)

        register(client, username="bob", email="bob@example.com")
        bob_headers = auth_headers(client, username="bob")
        request_phone(client, bob_headers, number=OTHER_PHONE)

        response = verify_phone(client, bob_headers, ada_code)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "PHONE_CODE_INVALID"


# ---------------------------------------------------------------------------
# Resending
# ---------------------------------------------------------------------------


def test_resending_without_a_number_is_rejected(
    tmp_path, sign_in_without_verifying_email
) -> None:
    with phone_client(str(tmp_path / "p.db")) as client:
        register(client)
        headers = auth_headers(client)
        response = client.post(RESEND_PHONE, headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "INVALID_PHONE_NUMBER"


def test_resending_reuses_the_stored_number(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "q.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        mutate_user(
            db_path,
            "ada",
            phone_verification_sent_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        response = client.post(RESEND_PHONE, headers=headers)
        verified = verify_phone(client, headers, sender.code_for(PHONE))

    assert response.status_code == 202, response.text
    assert sender.last.to == PHONE
    assert verified.status_code == 200, verified.text


def test_resending_inside_the_cooldown_sends_nothing(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "r.db"), sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        response = client.post(RESEND_PHONE, headers=headers)

    assert response.status_code == 202, response.text
    assert len(sender.sent) == 1
    assert response.json()["resend_available_in_seconds"] is not None


# ---------------------------------------------------------------------------
# Removing the number
# ---------------------------------------------------------------------------


def test_deleting_the_number_clears_the_flag_and_every_verification_column(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    db_path = str(tmp_path / "s.db")
    with phone_client(db_path, sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        verify_phone(client, headers, sender.code_for(PHONE))

        response = client.delete(DELETE_PHONE, headers=headers)
        stored = read_user(db_path)
        me = client.get("/api/users/me", headers=headers)

    assert response.status_code == 204, response.text
    assert stored.phone_number is None
    assert stored.phone_verified is False
    assert stored.phone_verified_at is None
    assert stored.phone_verification_code_hash is None
    assert stored.phone_verification_expires_at is None
    assert stored.phone_verification_attempts == 0
    # The profile the SPA renders must agree with the row.
    assert me.json()["phone_number"] is None
    assert me.json()["phone_verified"] is False


def test_the_number_can_be_verified_again_after_removal(
    tmp_path, sign_in_without_verifying_email
) -> None:
    sender = FakeSmsSender()
    with phone_client(str(tmp_path / "t.db"), sms_sender=sender) as client:
        register(client)
        headers = auth_headers(client)
        request_phone(client, headers)
        verify_phone(client, headers, sender.code_for(PHONE))
        client.delete(DELETE_PHONE, headers=headers)

        # A fresh request after removal starts from a clean slate (there is no
        # stale cooldown to suppress it, which the DELETE cleared).
        response = request_phone(client, headers)
        verified = verify_phone(client, headers, sender.code_for(PHONE))

    assert response.status_code == 202, response.text
    assert verified.status_code == 200, verified.text
    assert verified.json()["phone_verified"] is True
