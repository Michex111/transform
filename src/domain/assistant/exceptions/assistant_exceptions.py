"""Assistant-domain exceptions.

Mirrors ``domain/security/exceptions``: one base error per bounded context so a
caller can catch the whole family, plus specific subclasses for the cases the
presentation layer must translate into a *different* HTTP response.
"""


class AssistantError(Exception):
    """Base exception for assistant-domain errors."""


class AssistantQuotaExceeded(AssistantError):
    """The caller exhausted their hourly assistant allowance.

    Separate from :class:`AssistantDisabledError` because the status differs: a
    spent quota is a 429 (retry later), an unavailable tier is a 403 (this plan
    will never work).
    """


class AssistantDisabledError(AssistantError):
    """The caller's tier has no assistant allowance at all (quota is 0)."""


class AssistantToolError(AssistantError):
    """A tool could not do what it was asked (unknown file, unsupported edge).

    Raised only on paths where the caller is not the agent loop — the loop
    itself gets tool failures as ``{"error": ...}`` results so the model can
    explain them, while an explicit ``POST /summarize`` request has no model to
    explain anything and needs a real HTTP status.
    """


class AssistantConversationNotFound(AssistantError):
    """The requested conversation does not exist, or is not the caller's.

    One exception for both cases on purpose: telling them apart would let a
    caller enumerate other users' conversation ids by watching for a different
    response, which is exactly the leak the file and job endpoints avoid.
    """


class AssistantAttachmentNotFound(AssistantError):
    """A file attached to a chat message does not exist, or is not the caller's.

    Raised while the turn is still being set up (before any SSE frame is sent),
    so the router can answer with a real 404 instead of streaming an error. The
    "missing" and "not yours" cases are deliberately merged for the same reason
    as :class:`AssistantConversationNotFound`: a distinguishable response would
    let a caller probe whether another user's file id exists.
    """


class AssistantAttachmentLimitExceeded(AssistantError):
    """The message carries more attachments than the caller's tier allows.

    Separate from :class:`AssistantDisabledError` because the fix differs: the
    plan *does* include the assistant, the caller just sent more files than it
    permits per message. Raising (rather than silently truncating) is deliberate
    — attaching three files and having the model quietly see one would make the
    assistant look like it ignored what it was given. Raised before the stream
    starts, so the router answers with a real HTTP 403.
    """
