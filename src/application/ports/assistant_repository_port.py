"""Ports for assistant conversation storage and per-user metering.

Two small ports rather than one, because they have different lifetimes and
failure contracts: conversations are durable rows the caller expects to be able
to read back, while the hourly quota is a best-effort counter in Redis whose
failure must never take the feature down (see ``RedisAssistantQuota``).
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from src.domain.assistant.entities.conversation import Conversation, Message


@runtime_checkable
class ConversationRepositoryPort(Protocol):
    """Persists assistant conversations and their messages.

    Every read is scoped by ``user_id`` (where the method takes one) so a
    conversation id alone never grants access — the same no-enumeration rule the
    file and conversion APIs follow.
    """

    async def create_conversation(self, *, user_id: int, title: str) -> Conversation:
        """Create an empty conversation and return it (with its generated id)."""
        ...

    async def get_conversation(
        self, conversation_id: str, user_id: int
    ) -> Conversation | None:
        """Return the conversation when it exists AND belongs to ``user_id``."""
        ...

    async def list_conversations(
        self, user_id: int, *, limit: int = 50
    ) -> list[Conversation]:
        """The user's conversations, most recently updated first."""
        ...

    async def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        """Delete an owned conversation (and its messages). False when absent."""
        ...

    async def add_message(self, message: Message) -> Message:
        """Append a message, returning it with its assigned id/position/time."""
        ...

    async def list_messages(
        self, conversation_id: str, *, limit: int = 100
    ) -> Sequence[Message]:
        """The conversation's messages in ascending position order."""
        ...

    async def update_message_meta(self, message_id: str, meta: dict[str, Any]) -> bool:
        """Replace the stored ``meta`` of one message. False when the id is unknown.

        Exists so the deletion handshake can flip a ``delete`` artifact's state
        from ``pending`` to a resolved value on the message that carried it.
        Rewriting the meta (rather than appending a new message) is what stops a
        page reload from re-rendering a live Confirm prompt for a decision the
        user already made.
        """
        ...


@runtime_checkable
class AssistantQuotaPort(Protocol):
    """Meters assistant turns per user."""

    async def consume(self, user_id: int, limit: int) -> None:
        """Record one turn for ``user_id``.

        Raises :class:`AssistantQuotaExceeded` when ``limit`` is already
        exhausted. Implementations are expected to be atomic (the check and the
        increment must not be separable) or the limit is advisory only.
        """
        ...

    async def peek(self, user_id: int) -> int:
        """Turns already used in the current window, without recording one.

        Read-only and best-effort: it exists so ``GET /assistant/status`` can
        show usage to the client, so it must NOT create the counter and must NOT
        raise — an unavailable counter reports 0 (the same fail-open posture
        ``consume`` documents) rather than turning a status page into a 500.
        """
        ...
