"""Email transport selection and package exports."""

from src.infrastructure.adapters.email.console_email_adapter import ConsoleEmailAdapter
from src.infrastructure.adapters.email.resend_email_adapter import ResendEmailAdapter
from src.infrastructure.adapters.email.smtp_email_adapter import SMTPEmailAdapter

__all__ = [
    "ConsoleEmailAdapter",
    "ResendEmailAdapter",
    "SMTPEmailAdapter",
    "build_email_sender",
]


def build_email_sender(settings):
    """Return the ``EmailPort`` implementation for the configured transport.

    Takes the settings object explicitly (rather than calling ``get_settings``)
    so it stays importable from contexts that build their own settings, and so
    tests can drive every branch without touching the environment.

    The ``EMAIL_BACKEND`` value is resolved through ``settings._resolve_email_backend()``
    rather than read directly, so the ``auto`` rule (Resend > SMTP > console)
    lives in exactly one place.
    """
    backend = settings._resolve_email_backend()

    if backend == "resend":
        return ResendEmailAdapter(
            api_key=settings.RESEND_API_KEY.get_secret_value(),
            from_address=settings.EMAIL_FROM_ADDRESS,
            from_name=settings.EMAIL_FROM_NAME,
            reply_to=settings.EMAIL_REPLY_TO,
            api_url=settings.RESEND_API_URL,
            timeout_seconds=settings.EMAIL_HTTP_TIMEOUT_SECONDS,
        )

    if backend == "smtp":
        return SMTPEmailAdapter(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USERNAME,
            password=(
                settings.SMTP_PASSWORD.get_secret_value() if settings.SMTP_PASSWORD else None
            ),
            from_address=settings.EMAIL_FROM_ADDRESS,
            from_name=settings.EMAIL_FROM_NAME,
            reply_to=settings.EMAIL_REPLY_TO,
            use_starttls=settings.SMTP_USE_STARTTLS,
            use_ssl=settings.SMTP_USE_SSL,
            timeout_seconds=settings.SMTP_TIMEOUT_SECONDS,
        )

    return ConsoleEmailAdapter()
