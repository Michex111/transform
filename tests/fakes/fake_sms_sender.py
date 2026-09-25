"""Capturing SMS sender for tests.

Records every message instead of delivering it, and can be told to fail so the
"delivery failed, so the API must say so" path is exercised without a real
provider outage.
"""

import re

from src.application.dtos.sms_dto import SmsMessage

#: The code is the only run of six digits in the body. Matched rather than
#: sliced at a fixed offset so a copy change to the surrounding sentence cannot
#: silently break the recovery.
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


class FakeSmsSender:
    """An ``SmsPort`` that captures messages in memory."""

    def __init__(self) -> None:
        self.sent: list[SmsMessage] = []
        #: When set, ``send`` raises this instead of recording the message.
        self.failure: Exception | None = None

    async def send(self, message: SmsMessage) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)

    # -- Assertion helpers -------------------------------------------------
    @property
    def last(self) -> SmsMessage:
        assert self.sent, "no SMS was sent"
        return self.sent[-1]

    def code_for(self, recipient: str) -> str:
        """Extract the verification code from the most recent SMS to ``recipient``.

        Reads it back out of the *rendered* message rather than reaching into
        application state. That is the point of the whole test: the code only
        exists in plaintext in the message, so recovering it any other way would
        mean the production code had kept a copy it must not keep.
        """
        for message in reversed(self.sent):
            if message.to != recipient:
                continue
            match = _CODE_RE.search(message.body)
            assert match is not None, "SMS contains no six-digit code"
            return match.group(1)
        raise AssertionError(f"no SMS was sent to {recipient}")
