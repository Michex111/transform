"""Twilio SMS transport.

A single HTTP POST to Twilio's Messages resource rather than the ``twilio``
SDK: it is one form-encoded request, so a dependency would add supply-chain and
upgrade surface for no benefit (and ``uv lock --check`` must stay clean).

Two failure modes are spelled out because they are the ones that actually bite
in production:

* A non-2xx response body is where Twilio explains the problem — an unverified
  sender number, a trial-account restriction, an invalid ``To``, a geopermissions
  block. The error raised here always carries that body, because "HTTP 400"
  alone sends the operator hunting through logs for nothing.
* The credentials are never included in the exception text: an error is logged
  and may be surfaced to an operator, and the auth token is not a diagnostic.
"""

import logging

import httpx

from src.application.dtos.sms_dto import SmsMessage

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioSmsAdapter:
    """Sends SMS through Twilio's Programmable Messaging REST API."""

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        api_base: str = _TWILIO_API_BASE,
        timeout_seconds: int = 15,
        client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout_seconds
        # Injectable so tests exercise this class without patching httpx
        # globally. When omitted, a client is created and closed per send.
        self._client = client
        self._logger = logger or logging.getLogger("file_converter_api.sms")

    @property
    def _messages_url(self) -> str:
        return f"{self._api_base}/Accounts/{self._account_sid}/Messages.json"

    async def send(self, message: SmsMessage) -> None:
        # Form-encoded, not JSON: this resource predates Twilio's JSON API and
        # rejects a JSON body.
        data = {
            "To": message.to,
            "From": self._from_number,
            "Body": message.body,
        }
        # HTTP Basic with the account SID as username is Twilio's documented
        # auth for this endpoint (a bearer header is only used by the newer
        # Verify/Conversations APIs).
        auth = (self._account_sid, self._auth_token)

        if self._client is not None:
            response = await self._client.post(
                self._messages_url, data=data, auth=auth
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._messages_url, data=data, auth=auth
                )

        if response.status_code >= 400:
            # Twilio's body names the actual cause (unverified sender, trial
            # restriction, invalid number, geo permissions). Truncated so a
            # pathological response cannot flood the log.
            detail = response.text[:500]
            raise RuntimeError(
                f"Twilio rejected the SMS (HTTP {response.status_code}): {detail}"
            )

        self._logger.info("Verification SMS accepted by Twilio for %s", message.to)
