"""Capturing email sender for tests.

Records every message instead of delivering it, and can be told to fail so the
"delivery failed but the account still exists" path is exercised without a real
provider outage.
"""

from src.application.dtos.email_dto import EmailMessage


class FakeEmailSender:
    """An ``EmailPort`` that captures messages in memory."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        #: When set, ``send`` raises this instead of recording the message.
        self.failure: Exception | None = None

    async def send(self, message: EmailMessage) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)

    # -- Assertions helpers ------------------------------------------------
    @property
    def last(self) -> EmailMessage:
        assert self.sent, "no email was sent"
        return self.sent[-1]

    def verification_token_for(self, recipient: str) -> str:
        """Extract the raw token from the most recent email to ``recipient``.

        Reads it back out of the *rendered* message rather than reaching into
        application state: the token only exists in plaintext in the email, so
        recovering it any other way would mean the production code had kept a
        copy it should not keep.
        """
        for message in reversed(self.sent):
            if message.to != recipient:
                continue
            marker = "token="
            index = message.text_body.find(marker)
            assert index != -1, "verification email contains no token link"
            return message.text_body[index + len(marker) :].split()[0].strip()
        raise AssertionError(f"no email was sent to {recipient}")
