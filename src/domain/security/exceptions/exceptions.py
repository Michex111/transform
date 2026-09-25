"""Security-domain exceptions.

Mirrors the layout used by ``domain/conversions/exceptions`` and
``domain/subscriptions/exceptions``: one base error per bounded context so a
caller can catch the whole family, plus specific subclasses for the cases that
the application layer has to translate into a distinct HTTP response.
"""


class SecurityDomainError(Exception):
    """Base exception for security-domain errors."""


class InvalidPhoneNumber(SecurityDomainError):
    """Raised when a phone number cannot be normalised to E.164.

    A domain error rather than a ``ValueError``: the API layer maps it to a
    structured 400 (``INVALID_PHONE_NUMBER``) with a user-facing message, and
    that mapping must not depend on a bare builtin being raised anywhere in the
    call path.
    """
