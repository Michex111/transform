"""Transport-neutral representation of an outbound SMS.

Adapters (console, Twilio, ...) receive one of these and are responsible for
turning it into their provider's wire format. Keeping the message a plain value
object means the application layer never has to know which transport is
configured, and a new transport does not need changes to any caller.

There is only one body: unlike email there is no HTML/text split, and no
subject. An SMS is a single string of at most a few segments.
"""

from pydantic import BaseModel, Field


class SmsMessage(BaseModel):
    """A single transactional SMS."""

    to: str = Field(..., description="Recipient number in E.164 format")
    body: str = Field(
        ...,
        description=(
            "The message text. Keep it within a single 160-character GSM-7 "
            "segment: concatenated SMS is billed per segment and can arrive "
            "out of order or delayed."
        ),
    )
