"""Log-only email transport.

Used when no real transport is configured (``EMAIL_BACKEND=console``, or
``auto`` with no credentials). It logs the full plain-text body, which includes
the verification URL, so the whole signup → verify → sign-in flow can be
exercised locally without any provider account.

It never raises: "delivery" is a log write, so it cannot fail in a way that
would make a local registration request fail.
"""

import logging

from src.application.dtos.email_dto import EmailMessage


class ConsoleEmailAdapter:
    """Writes the message to the log instead of sending it."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("file_converter_api.email")

    async def send(self, message: EmailMessage) -> None:
        # WARNING, not INFO: this transport means real users are not receiving
        # real mail, and INFO is filtered out of the production log stream
        # (root logger level is WARNING there), so an INFO banner would be
        # invisible exactly where it matters.
        self._logger.warning(
            "EMAIL NOT SENT (console transport) — to=%s subject=%r\n%s",
            message.to,
            message.subject,
            message.text_body,
        )
