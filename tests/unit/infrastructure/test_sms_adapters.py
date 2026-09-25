"""Unit tests for the SMS adapters (console + Twilio).

The Twilio transport is the one component in the phone-verification flow that
spends real money and talks to a third party, so its wire format is pinned with
an ``httpx.MockTransport`` rather than left to a manual smoke test: a wrong auth
scheme or a JSON body would fail every send in production and only be discovered
by a user complaining that no text arrived.
"""

import asyncio
import base64
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from src.application.dtos.sms_dto import SmsMessage
from src.infrastructure.adapters.sms.console_sms_adapter import ConsoleSmsAdapter
from src.infrastructure.adapters.sms.twilio_sms_adapter import TwilioSmsAdapter

SID = "AC" + "0" * 32
TOKEN = "super-secret-auth-token"
FROM = "+14155552671"
TO = "+442079460958"


def _message() -> SmsMessage:
    return SmsMessage(to=TO, body="Transform: your verification code is 123456.")


# ---------------------------------------------------------------------------
# Console adapter
# ---------------------------------------------------------------------------


def test_console_adapter_logs_the_body_so_the_code_is_recoverable(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        asyncio.run(ConsoleSmsAdapter().send(_message()))

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert TO in text
    assert "123456" in text


def test_console_adapter_logs_at_warning_not_info(caplog) -> None:
    """INFO is dropped from the production log stream, so it would be invisible."""
    with caplog.at_level(logging.INFO):
        asyncio.run(ConsoleSmsAdapter().send(_message()))

    relevant = [r for r in caplog.records if TO in r.getMessage()]
    assert relevant, "nothing was logged"
    assert all(r.levelno >= logging.WARNING for r in relevant)


# ---------------------------------------------------------------------------
# Twilio adapter
# ---------------------------------------------------------------------------


def _adapter(handler, **overrides) -> TwilioSmsAdapter:
    kwargs = {
        "account_sid": SID,
        "auth_token": TOKEN,
        "from_number": FROM,
    }
    kwargs.update(overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TwilioSmsAdapter(client=client, **kwargs)


def test_twilio_posts_to_the_accounts_messages_resource() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        return httpx.Response(201, json={"sid": "SM123"})

    asyncio.run(_adapter(handler).send(_message()))

    assert captured["method"] == "POST"
    assert captured["url"] == (
        f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    )


def test_twilio_uses_http_basic_auth_with_the_account_sid() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(201, json={"sid": "SM123"})

    asyncio.run(_adapter(handler).send(_message()))

    header = captured["authorization"]
    assert header is not None and header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == f"{SID}:{TOKEN}"


def test_twilio_form_encodes_to_from_and_body() -> None:
    """This resource predates Twilio's JSON API and rejects a JSON body."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["form"] = parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "SM123"})

    asyncio.run(_adapter(handler).send(_message()))

    assert "application/x-www-form-urlencoded" in captured["content_type"]
    assert captured["form"]["To"] == [TO]
    assert captured["form"]["From"] == [FROM]
    assert captured["form"]["Body"] == [_message().body]


def test_twilio_raises_on_a_non_2xx_and_surfaces_twilios_explanation() -> None:
    """Twilio's body is where the real cause is (unverified sender, trial caps)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"code": 21608, "message": "unverified number"}')

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(_adapter(handler).send(_message()))

    message = str(exc_info.value)
    assert "400" in message
    assert "unverified number" in message


def test_twilio_never_leaks_the_auth_token_in_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Authenticate")

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(_adapter(handler).send(_message()))

    assert TOKEN not in str(exc_info.value)


def test_twilio_truncates_a_pathological_response_body() -> None:
    """A huge error page must not flood the log with a single line."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 5_000)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(_adapter(handler).send(_message()))

    assert len(str(exc_info.value)) < 1_000


def test_twilio_error_never_includes_the_body_content() -> None:
    """The error must not echo the message (which contains the live code)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text='{"message": "bad To"}')

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(_adapter(handler).send(_message()))

    assert "123456" not in str(exc_info.value)


@pytest.mark.parametrize("status_code", [200, 201, 202])
def test_twilio_accepts_any_2xx(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"sid": "SM123"})

    asyncio.run(_adapter(handler).send(_message()))  # must not raise
