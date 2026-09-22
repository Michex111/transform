"""Unit tests for the email transport adapters and the transport factory.

No network or SMTP server is involved: ``smtplib`` is replaced with a recording
double and the Resend adapter is given an ``httpx.MockTransport``. That makes the
assertions about *what would be sent* exact — headers, MIME structure, payload —
which is the part that is otherwise only discoverable from a real inbox.
"""

import asyncio
import smtplib

import httpx
import pytest

from src.application.dtos.email_dto import EmailMessage
from src.infrastructure.adapters.email.console_email_adapter import ConsoleEmailAdapter
from src.infrastructure.adapters.email.factory import build_email_sender
from src.infrastructure.adapters.email.resend_email_adapter import ResendEmailAdapter
from src.infrastructure.adapters.email.smtp_email_adapter import SMTPEmailAdapter
from src.infrastructure.config.settings import Settings


def _message(**overrides) -> EmailMessage:
    kwargs = {
        "to": "user@example.com",
        "subject": "Verify your email address",
        "html_body": "<p>Hello <b>there</b></p>",
        "text_body": "Hello there\n\nhttps://app.test/verify-email?token=abc",
    }
    kwargs.update(overrides)
    return EmailMessage(**kwargs)


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
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Console adapter
# ---------------------------------------------------------------------------


def test_console_adapter_logs_the_plain_text_body(caplog) -> None:
    """The whole point of this transport is that the link is visible in the log."""
    import logging

    adapter = ConsoleEmailAdapter()

    with caplog.at_level(logging.WARNING):
        asyncio.run(adapter.send(_message()))

    assert any("user@example.com" in r.getMessage() for r in caplog.records)
    assert any("token=abc" in r.getMessage() for r in caplog.records)


def test_console_adapter_logs_at_warning_not_info(caplog) -> None:
    """INFO is dropped from the production log stream, so it would be invisible."""
    import logging

    adapter = ConsoleEmailAdapter()

    with caplog.at_level(logging.INFO):
        asyncio.run(adapter.send(_message()))

    relevant = [r for r in caplog.records if "user@example.com" in r.getMessage()]
    assert relevant, "nothing was logged"
    assert all(r.levelno >= logging.WARNING for r in relevant)


# ---------------------------------------------------------------------------
# SMTP adapter
# ---------------------------------------------------------------------------


class RecordingSMTP:
    """Stands in for ``smtplib.SMTP`` / ``SMTP_SSL``."""

    instances: list["RecordingSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.calls: list[str] = []
        self.logins: list[tuple[str, str]] = []
        self.message = None
        RecordingSMTP.instances.append(self)

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self, context=None) -> None:
        self.calls.append("starttls")

    def login(self, username, password) -> None:
        self.calls.append("login")
        self.logins.append((username, password))

    def send_message(self, message) -> None:
        self.calls.append("send_message")
        self.message = message

    def quit(self) -> None:
        self.calls.append("quit")


@pytest.fixture(autouse=True)
def _reset_recording_smtp():
    RecordingSMTP.instances = []
    yield
    RecordingSMTP.instances = []


def _patch_smtp(monkeypatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP", RecordingSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", RecordingSMTP)


def _smtp_adapter(**overrides) -> SMTPEmailAdapter:
    kwargs = {
        "host": "smtp.example.com",
        "port": 587,
        "username": "apikey",
        "password": "secret",
        "from_address": "no-reply@example.com",
        "from_name": "Transform",
    }
    kwargs.update(overrides)
    return SMTPEmailAdapter(**kwargs)


def test_smtp_sends_a_multipart_alternative_with_text_before_html(monkeypatch) -> None:
    """Clients render the LAST part they understand, so HTML must follow text."""
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter().send(_message()))
    sent = RecordingSMTP.instances[0].message

    assert sent.get_content_type() == "multipart/alternative"
    parts = sent.get_payload()
    assert len(parts) == 2
    assert parts[0].get_content_type() == "text/plain"
    assert parts[1].get_content_type() == "text/html"
    assert "token=abc" in parts[0].get_payload()


def test_smtp_sets_the_envelope_and_content_headers(monkeypatch) -> None:
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter().send(_message()))
    sent = RecordingSMTP.instances[0].message

    assert sent["To"] == "user@example.com"
    assert sent["From"] == "Transform <no-reply@example.com>"
    assert sent["Subject"] == "Verify your email address"
    # A Message-ID on our own domain improves deliverability and lets providers
    # de-duplicate; its absence is a common cause of spam scoring.
    assert sent["Message-ID"]
    assert "example.com" in sent["Message-ID"]


def test_smtp_uses_starttls_and_authenticates(monkeypatch) -> None:
    _patch_smtp(monkeypatch)

    asyncio.run(
        _smtp_adapter(use_starttls=True, use_ssl=False).send(_message())
    )
    server = RecordingSMTP.instances[0]

    assert "starttls" in server.calls
    assert server.logins == [("apikey", "secret")]
    assert "quit" in server.calls


def test_smtp_uses_implicit_tls_without_starttls(monkeypatch) -> None:
    """Port 465 is already TLS; upgrading again would fail the handshake."""
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter(use_starttls=False, use_ssl=True).send(_message()))
    server = RecordingSMTP.instances[0]

    assert "starttls" not in server.calls
    assert "send_message" in server.calls


def test_smtp_skips_login_when_no_credentials(monkeypatch) -> None:
    """Some relays (IP-allowlisted internal ones) accept unauthenticated mail."""
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter(username=None, password=None).send(_message()))

    assert RecordingSMTP.instances[0].logins == []


def test_smtp_applies_the_configured_timeout(monkeypatch) -> None:
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter(timeout_seconds=7).send(_message()))

    assert RecordingSMTP.instances[0].timeout == 7


def test_smtp_quit_failure_does_not_mask_a_successful_send(monkeypatch) -> None:
    """A broken connection during QUIT must not fail an already-delivered email."""

    class FailingQuit(RecordingSMTP):
        def quit(self) -> None:
            raise OSError("connection reset")

    monkeypatch.setattr(smtplib, "SMTP", FailingQuit)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FailingQuit)

    asyncio.run(_smtp_adapter().send(_message()))  # must not raise

    assert "send_message" in FailingQuit.instances[0].calls


def test_smtp_honours_a_per_message_reply_to(monkeypatch) -> None:
    _patch_smtp(monkeypatch)

    asyncio.run(_smtp_adapter().send(_message(reply_to="support@example.com")))

    assert RecordingSMTP.instances[0].message["Reply-To"] == "support@example.com"


# ---------------------------------------------------------------------------
# Resend adapter
# ---------------------------------------------------------------------------


def _resend_adapter(handler, **overrides) -> ResendEmailAdapter:
    kwargs = {
        "api_key": "re_secret_key",
        "from_address": "no-reply@example.com",
        "from_name": "Transform",
    }
    kwargs.update(overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ResendEmailAdapter(client=client, **kwargs)


def test_resend_posts_the_expected_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"id": "email_123"})

    asyncio.run(_resend_adapter(handler).send(_message()))

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_secret_key"

    body = captured["body"]
    assert body["from"] == "Transform <no-reply@example.com>"
    assert body["to"] == ["user@example.com"]
    assert body["subject"] == "Verify your email address"
    # Both parts must be sent: Resend would otherwise deliver HTML-only mail.
    assert "token=abc" in body["text"]
    assert body["html"] == "<p>Hello <b>there</b></p>"
    assert "reply_to" not in body


def test_resend_includes_reply_to_only_when_set() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"id": "x"})

    asyncio.run(
        _resend_adapter(handler).send(_message(reply_to="support@example.com"))
    )

    assert captured["body"]["reply_to"] == "support@example.com"


def test_resend_raises_with_the_provider_message_on_rejection() -> None:
    """A 401 (bad key) and a 403 (unverified domain) look identical without this."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"statusCode": 403, "message": "Domain not verified"},
        )

    with pytest.raises(RuntimeError, match="Domain not verified"):
        asyncio.run(_resend_adapter(handler).send(_message()))


def test_resend_error_names_the_status_and_never_leaks_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(_resend_adapter(handler).send(_message()))

    message = str(exc_info.value)
    assert "401" in message
    assert "invalid api key" in message
    assert "re_secret_key" not in message


def test_resend_omits_the_display_name_when_blank() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"id": "x"})

    asyncio.run(_resend_adapter(handler, from_name="").send(_message()))

    # " <addr>" with a stray space is a malformed From header.
    assert captured["body"]["from"] == "no-reply@example.com"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_builds_the_console_adapter_when_unconfigured() -> None:
    assert isinstance(build_email_sender(_settings()), ConsoleEmailAdapter)


def test_factory_builds_the_resend_adapter_from_configuration() -> None:
    sender = build_email_sender(_settings(RESEND_API_KEY="re_123"))

    assert isinstance(sender, ResendEmailAdapter)


def test_factory_builds_the_smtp_adapter_from_configuration() -> None:
    sender = build_email_sender(_settings(SMTP_HOST="smtp.example.com", EMAIL_BACKEND="smtp"))

    assert isinstance(sender, SMTPEmailAdapter)


def test_factory_honours_an_explicit_console_choice_despite_credentials() -> None:
    """An explicit choice is the operator's; it must not be overridden by `auto`."""
    sender = build_email_sender(
        _settings(EMAIL_BACKEND="console", RESEND_API_KEY="re_123")
    )

    assert isinstance(sender, ConsoleEmailAdapter)
