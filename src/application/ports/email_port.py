"""Dependency-inversion port for outbound transactional email."""

from typing import Protocol, runtime_checkable

from src.application.dtos.email_dto import EmailMessage


@runtime_checkable
class EmailPort(Protocol):
    """Sends transactional email.

    Implementations must be safe to call from async request handlers: the
    transports here are blocking (``smtplib``) or network-bound (``httpx``), so
    each adapter is responsible for not blocking the event loop.
    """

    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message``.

        Raises an exception when delivery fails. Callers that must not fail the
        surrounding operation (e.g. registration) are expected to catch it
        explicitly — see ``send_verification_email`` in the users router — so
        that "we could not send" is a decision at the call site rather than a
        silent swallow inside the adapter.
        """
        ...
