"""Resend email transport (https://resend.com).

A small HTTP POST to Resend's ``/emails`` endpoint rather than an SDK: it is one
request with a JSON body, so a dependency would add supply-chain and upgrade
surface for no benefit.

Two failure modes are spelled out because they are the ones that actually bite
in production:

* A non-2xx response body can echo back request context, and the ``401``
  response in particular is easy to misread as "the code is broken". The error
  raised here always names the status and Resend's own message, and never
  includes the API key.
* Resend returns ``200`` for an accepted message, not for a delivered one. A
  valid API key with an unverified ``from`` domain still fails the send, so the
  response body is surfaced rather than discarded.
"""

import logging

import httpx

from src.application.dtos.email_dto import EmailMessage


class ResendEmailAdapter:
    """Sends mail through the Resend HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        from_name: str = "",
        reply_to: str | None = None,
        api_url: str = "https://api.resend.com/emails",
        timeout_seconds: int = 15,
        client: httpx.AsyncClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api_key = api_key
        self._from_address = from_address
        self._from_name = from_name
        self._reply_to = reply_to
        self._api_url = api_url
        self._timeout = timeout_seconds
        # Injectable so tests exercise this class without patching httpx
        # globally. When omitted, a client is created and closed per send.
        self._client = client
        self._logger = logger or logging.getLogger("file_converter_api.email")

    @property
    def _from_header(self) -> str:
        return f"{self._from_name} <{self._from_address}>" if self._from_name else self._from_address

    async def send(self, message: EmailMessage) -> None:
        payload: dict[str, object] = {
            "from": self._from_header,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html_body,
            "text": message.text_body,
        }
        reply_to = message.reply_to or self._reply_to
        if reply_to:
            payload["reply_to"] = reply_to

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        if self._client is not None:
            response = await self._client.post(self._api_url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._api_url, json=payload, headers=headers)

        if response.status_code >= 400:
            # Include Resend's message, which distinguishes the common causes
            # (unverified domain, invalid key, rate limit) without leaking the
            # credential itself.
            detail = response.text[:500]
            raise RuntimeError(
                f"Resend rejected the email (HTTP {response.status_code}): {detail}"
            )

        self._logger.info("Verification email accepted by Resend for %s", message.to)
