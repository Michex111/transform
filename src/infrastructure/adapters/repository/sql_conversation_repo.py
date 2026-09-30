"""SQLAlchemy repository for assistant conversations and messages.

The only place that knows the transcript is stored with ``meta`` as a JSON
string and ``position`` as an integer: callers get domain entities, so the
storage representation can change without touching the assistant service.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.assistant.entities.conversation import (
    Conversation,
    Message,
    MessageRole,
)
from src.infrastructure.database.models import AiConversationModel, AiMessageModel

logger = logging.getLogger(__name__)


def _to_conversation(row: AiConversationModel) -> Conversation:
    return Conversation(
        id=row.id,
        user_id=row.user_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_message(row: AiMessageModel) -> Message:
    meta: dict[str, Any] | None = None
    if row.meta:
        try:
            decoded: Any = json.loads(row.meta)
        except ValueError:
            # A corrupt meta blob must not make the whole conversation
            # unreadable: the message text is what the user sees, and losing the
            # tool-call bookkeeping only costs one request's tool pairing.
            logger.warning("Discarding undecodable assistant message meta %r", row.id)
        else:
            if isinstance(decoded, dict):
                meta = decoded
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        position=row.position,
        role=MessageRole(row.role),
        content=row.content,
        tool_name=row.tool_name,
        meta=meta,
        created_at=row.created_at,
    )


class SQLConversationRepository:
    """Persists assistant conversations and their messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_conversation(self, *, user_id: int, title: str) -> Conversation:
        """Create an empty conversation. Returns it with its generated id."""
        now = datetime.now(UTC)
        row = AiConversationModel(
            id=str(uuid4()),
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        return _to_conversation(row)

    async def get_conversation(
        self, conversation_id: str, user_id: int
    ) -> Conversation | None:
        """Fetch an owned conversation.

        The ``user_id`` predicate is part of the query rather than a check after
        the fetch, so a foreign conversation is never even loaded into memory.
        """
        result = await self._session.execute(
            select(AiConversationModel).where(
                AiConversationModel.id == conversation_id,
                AiConversationModel.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        return _to_conversation(row) if row is not None else None

    async def list_conversations(
        self, user_id: int, *, limit: int = 50
    ) -> list[Conversation]:
        """The user's conversations, most recently updated first."""
        result = await self._session.execute(
            select(AiConversationModel)
            .where(AiConversationModel.user_id == user_id)
            .order_by(AiConversationModel.updated_at.desc())
            .limit(limit)
        )
        return [_to_conversation(row) for row in result.scalars().all()]

    async def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        """Delete an owned conversation and its messages. False when absent.

        Messages are deleted explicitly rather than relying on the foreign key's
        ``ON DELETE CASCADE``: SQLite (used by the test suite) does not enforce
        foreign keys unless the pragma is on, so a cascade that only works in
        PostgreSQL would leave orphaned transcripts behind in tests.
        """
        if await self.get_conversation(conversation_id, user_id) is None:
            return False
        await self._session.execute(
            delete(AiMessageModel).where(AiMessageModel.conversation_id == conversation_id)
        )
        await self._session.execute(
            delete(AiConversationModel).where(
                AiConversationModel.id == conversation_id,
                AiConversationModel.user_id == user_id,
            )
        )
        await self._session.commit()
        return True

    async def truncate_from_message(
        self, conversation_id: str, message_id: str
    ) -> bool:
        """Delete ``message_id`` and every message after it in the same conversation.

        Returns False when no such message exists in that conversation, so the
        caller cannot distinguish "absent" from "belongs to another conversation".

        Deleting a *suffix* (``position >= target``) is what keeps
        :meth:`_next_position`'s ``COUNT`` correct: the rows left behind are
        exactly ``0..n-1`` with no gaps, so the next append lands last. The
        ``(conversation_id, position)`` index is not unique, so removing rows
        cannot violate a constraint either.
        """
        result = await self._session.execute(
            select(AiMessageModel.position).where(
                AiMessageModel.id == message_id,
                AiMessageModel.conversation_id == conversation_id,
            )
        )
        target_position = result.scalar_one_or_none()
        if target_position is None:
            return False
        await self._session.execute(
            delete(AiMessageModel).where(
                AiMessageModel.conversation_id == conversation_id,
                AiMessageModel.position >= target_position,
            )
        )
        await self._session.commit()
        return True

    async def add_message(self, message: Message) -> Message:
        """Append a message, assigning its position within the conversation.

        The position is computed with a ``COUNT`` in the same transaction as the
        insert, which is what the ``(conversation_id, position)`` index supports.
        Two concurrent appends to one conversation are already an ordering
        question with no right answer (the model's own replies are sequential),
        so no stronger guarantee is attempted.
        """
        next_position = await self._next_position(message.conversation_id)
        row = AiMessageModel(
            id=message.id or str(uuid4()),
            conversation_id=message.conversation_id,
            position=next_position,
            role=str(message.role),
            content=message.content,
            tool_name=message.tool_name,
            meta=json.dumps(message.meta, default=str) if message.meta else None,
            created_at=message.created_at or datetime.now(UTC),
        )
        self._session.add(row)
        # Touching ``updated_at`` is what keeps the conversation list ordered by
        # real activity; it happens in the same commit as the message so the two
        # can never disagree.
        await self._touch_conversation(message.conversation_id)
        await self._session.commit()
        return _to_message(row)

    async def list_messages(
        self, conversation_id: str, *, limit: int = 100
    ) -> Sequence[Message]:
        """The newest ``limit`` messages of a conversation, oldest first.

        The window is applied newest-first (so the cap keeps the *recent* tail)
        and then reversed, which is the order both the UI and the prompt want.
        """
        result = await self._session.execute(
            select(AiMessageModel)
            .where(AiMessageModel.conversation_id == conversation_id)
            .order_by(AiMessageModel.position.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
        rows.reverse()
        return [_to_message(row) for row in rows]

    async def update_message_meta(self, message_id: str, meta: dict[str, Any]) -> bool:
        """Replace the stored ``meta`` of one message. False when the id is absent.

        A whole-column overwrite (rather than a JSON merge) because the caller
        already holds the full, reconstructed meta dict from
        :meth:`list_messages`, and a merge would leave a stale nested key behind
        if the caller dropped one. ``None`` clears the column, matching the
        storage layout used by :meth:`add_message`.
        """
        row = await self._session.get(AiMessageModel, message_id)
        if row is None:
            return False
        row.meta = json.dumps(meta, default=str) if meta else None
        await self._session.commit()
        return True

    async def _next_position(self, conversation_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(AiMessageModel)
            .where(AiMessageModel.conversation_id == conversation_id)
        )
        return int(result.scalar_one())

    async def _touch_conversation(self, conversation_id: str) -> None:
        conversation = await self._session.get(AiConversationModel, conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)
