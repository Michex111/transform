"""Dependency-inversion port for outbound SMS."""

from typing import Protocol, runtime_checkable

from src.application.dtos.sms_dto import SmsMessage


@runtime_checkable
class SmsPort(Protocol):
    """Sends transactional SMS.

    Implementations must be safe to call from async request handlers: the
    transports here are network-bound (``httpx``), so each adapter is
    responsible for not blocking the event loop.

    Unlike ``EmailPort``, a failure here is *not* swallowed by the caller in the
    phone-verification flow: the user is actively waiting for a code, so a
    request that reports 202 while nothing was delivered is a lie. The API
    returns 503 instead — see ``request_phone_verification`` in the users
    router.
    """

    async def send(self, message: SmsMessage) -> None:
        """Deliver ``message``.

        Raises an exception when delivery fails; callers decide whether that
        fails the request.
        """
        ...
