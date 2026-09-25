"""SMS transport selection and package exports."""

from src.infrastructure.adapters.sms.console_sms_adapter import ConsoleSmsAdapter
from src.infrastructure.adapters.sms.twilio_sms_adapter import TwilioSmsAdapter

__all__ = [
    "ConsoleSmsAdapter",
    "TwilioSmsAdapter",
    "build_sms_sender",
]


def build_sms_sender(settings):
    """Return the ``SmsPort`` implementation for the configured transport.

    Takes the settings object explicitly (rather than calling ``get_settings``)
    so it stays importable from contexts that build their own settings, and so
    tests can drive every branch without touching the environment.

    The ``SMS_BACKEND`` value is resolved through ``settings._resolve_sms_backend()``
    rather than read directly, so the ``auto`` rule lives in exactly one place.

    ``Settings.validate()`` already refuses an explicit ``twilio`` that is
    missing credentials, so the ``twilio`` branch here can assume they exist —
    but the assertion is not duplicated: a missing credential raises at boot,
    which is the only place it can be reported usefully.
    """
    backend = settings._resolve_sms_backend()

    if backend == "twilio":
        return TwilioSmsAdapter(
            account_sid=settings.TWILIO_ACCOUNT_SID.get_secret_value(),
            auth_token=settings.TWILIO_AUTH_TOKEN.get_secret_value(),
            from_number=settings.TWILIO_FROM_NUMBER,
            timeout_seconds=settings.SMS_HTTP_TIMEOUT_SECONDS,
        )

    return ConsoleSmsAdapter()
