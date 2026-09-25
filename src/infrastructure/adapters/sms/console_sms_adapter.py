"""Log-only SMS transport.

Used when no real transport is configured (``SMS_BACKEND=console``, or ``auto``
with no credentials). It logs the full body, which includes the verification
code, so the whole request → verify flow can be exercised locally without a
provider account — and, importantly, so the code is still recoverable by the
developer running the app.

It never raises: "delivery" is a log write, so it cannot fail in a way that
would turn a local request into a 503.
"""

import logging

from src.application.dtos.sms_dto import SmsMessage


class ConsoleSmsAdapter:
    """Writes the message to the log instead of sending it."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("file_converter_api.sms")

    async def send(self, message: SmsMessage) -> None:
        # WARNING, not INFO, for the same reason the email console adapter uses
        # it: this transport means real users are not receiving real texts, and
        # INFO is filtered out of the production log stream (root logger level
        # is WARNING there), so an INFO banner would be invisible exactly where
        # it matters. It is also what makes the code visible in the API log.
        self._logger.warning(
            "SMS NOT SENT (console transport) — to=%s\n%s",
            message.to,
            message.body,
        )
