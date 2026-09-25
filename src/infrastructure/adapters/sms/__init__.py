"""Transactional SMS adapters.

Import from the concrete modules (``console_sms_adapter``, ...) rather than
this package when you need a specific class; this module re-exports the public
surface for convenience.
"""

from src.infrastructure.adapters.sms.factory import (
    ConsoleSmsAdapter,
    TwilioSmsAdapter,
    build_sms_sender,
)

__all__ = [
    "ConsoleSmsAdapter",
    "TwilioSmsAdapter",
    "build_sms_sender",
]
