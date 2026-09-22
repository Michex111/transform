"""Transactional email adapters.

Import from the concrete modules (``console_email_adapter``, ...) rather than
this package when you need a specific class; this module re-exports the public
surface for convenience.
"""

from src.infrastructure.adapters.email.factory import (
    ConsoleEmailAdapter,
    ResendEmailAdapter,
    SMTPEmailAdapter,
    build_email_sender,
)

__all__ = [
    "ConsoleEmailAdapter",
    "ResendEmailAdapter",
    "SMTPEmailAdapter",
    "build_email_sender",
]
