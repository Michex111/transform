"""Transport-neutral representation of an outbound email.

Adapters (console, SMTP, Resend, ...) receive one of these and are responsible
for turning it into their provider's wire format. Keeping the message a plain
value object means the application layer never has to know which transport is
configured, and a new transport does not need changes to any caller.
"""

from pydantic import BaseModel, Field


class EmailMessage(BaseModel):
    """A single transactional email.

    ``text_body`` is required rather than derived. A ``text/plain`` alternative
    is not a nicety: HTML-only mail is a well-known spam signal, and it is the
    only part a text-mode or screen-reader client can use. Callers are expected
    to supply a hand-written plain-text version that carries the same
    information (notably the raw URL), because an auto-stripped HTML rendering
    loses the link.
    """

    to: str = Field(..., description="Recipient email address")
    subject: str
    html_body: str = Field(..., description="HTML body (the rich version)")
    text_body: str = Field(..., description="Plain-text alternative part")
    reply_to: str | None = Field(
        default=None,
        description="Optional Reply-To; falls back to the sender when unset",
    )
