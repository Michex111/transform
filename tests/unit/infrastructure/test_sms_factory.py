"""Unit tests for SMS transport selection.

The ``auto`` rule matters operationally: it decides whether a deployment sends
real texts or writes codes to the log, so each branch is pinned here rather than
inferred from a boot log.
"""

import pytest

from src.infrastructure.adapters.sms import (
    ConsoleSmsAdapter,
    TwilioSmsAdapter,
    build_sms_sender,
)
from src.infrastructure.config.settings import Settings

SID = "AC" + "0" * 32
TOKEN = "twilio-auth-token-value"
FROM = "+14155552671"


def _settings(**overrides) -> Settings:
    base = {
        "ENVIRONMENT": "development",
        "SECRET_KEY": "s" * 40,
        "REDIS_URL": "redis://localhost:6379/0",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "BACKBLAZE_ENDPOINT": "s3.amazonaws.com",
        "BACKBLAZE_ACCESS_KEY": "ak",
        "BACKBLAZE_SECRET_KEY": "sk",
        "BASE_TARGET_KEY": "output/",
        # Pinned so the developer's own .env cannot decide which branch this
        # test exercises.
        "SMS_BACKEND": "auto",
        "TWILIO_ACCOUNT_SID": None,
        "TWILIO_AUTH_TOKEN": None,
        "TWILIO_FROM_NUMBER": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


_CREDENTIALS = {
    "TWILIO_ACCOUNT_SID": SID,
    "TWILIO_AUTH_TOKEN": TOKEN,
    "TWILIO_FROM_NUMBER": FROM,
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_auto_resolves_to_console_without_credentials() -> None:
    settings = _settings()

    assert settings.SMS_BACKEND == "auto"
    assert settings._resolve_sms_backend() == "console"
    assert isinstance(build_sms_sender(settings), ConsoleSmsAdapter)


def test_auto_resolves_to_twilio_with_all_three_credentials() -> None:
    settings = _settings(**_CREDENTIALS)

    assert settings._resolve_sms_backend() == "twilio"
    assert isinstance(build_sms_sender(settings), TwilioSmsAdapter)


@pytest.mark.parametrize(
    "missing",
    ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"],
)
def test_auto_does_not_resolve_to_twilio_with_incomplete_credentials(missing: str) -> None:
    """A half-configured Twilio would fail every send; the log sink is better.

    ``Settings.validate()`` warns about this case explicitly, because there is
    no gate here that fails open — nothing else would surface it.
    """
    credentials = dict(_CREDENTIALS)
    credentials[missing] = None

    settings = _settings(**credentials)

    assert settings._resolve_sms_backend() == "console"


def test_an_empty_from_number_does_not_count_as_configured() -> None:
    settings = _settings(**{**_CREDENTIALS, "TWILIO_FROM_NUMBER": ""})

    assert settings._resolve_sms_backend() == "console"


# ---------------------------------------------------------------------------
# Explicit choices
# ---------------------------------------------------------------------------


def test_explicit_console_wins_even_with_credentials() -> None:
    """An operator who asks for the log sink must get it (e.g. a staging box)."""
    settings = _settings(SMS_BACKEND="console", **_CREDENTIALS)

    assert settings._resolve_sms_backend() == "console"
    assert isinstance(build_sms_sender(settings), ConsoleSmsAdapter)


def test_explicit_twilio_is_honoured() -> None:
    settings = _settings(SMS_BACKEND="twilio", **_CREDENTIALS)

    assert settings._resolve_sms_backend() == "twilio"
    assert isinstance(build_sms_sender(settings), TwilioSmsAdapter)


def test_an_unsupported_backend_is_rejected() -> None:
    """A typo must not silently resolve to a transport nobody intended."""
    with pytest.raises(ValueError, match="Unsupported SMS_BACKEND"):
        _settings(SMS_BACKEND="signal").validate()


def test_explicit_twilio_without_credentials_is_a_startup_error() -> None:
    """An explicit choice is never silently downgraded to the log sink."""
    with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
        _settings(SMS_BACKEND="twilio").validate()


def test_explicit_twilio_missing_only_the_token_is_a_startup_error() -> None:
    with pytest.raises(RuntimeError, match="TWILIO_AUTH_TOKEN"):
        _settings(
            SMS_BACKEND="twilio",
            TWILIO_ACCOUNT_SID=SID,
            TWILIO_FROM_NUMBER=FROM,
        ).validate()


def test_a_supported_backend_validates_cleanly() -> None:
    _settings(SMS_BACKEND="console").validate()  # should not raise
    _settings(**_CREDENTIALS).validate()  # auto -> twilio, complete


def test_a_non_positive_attempt_ceiling_is_rejected() -> None:
    """Zero attempts would make a 6-digit code instantly brute-forceable."""
    with pytest.raises(RuntimeError, match="PHONE_VERIFICATION_MAX_ATTEMPTS"):
        _settings(PHONE_VERIFICATION_MAX_ATTEMPTS=0).validate()
