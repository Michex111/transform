"""Assistant conversation ORM models.

The transcript is stored as rows rather than as a JSON blob on the conversation
for two reasons that matter in production: the history window is a query with a
``LIMIT`` instead of "read the entire blob and slice it", and one malformed
message cannot corrupt a conversation's whole history.

``meta`` is a JSON *string* in a ``Text`` column, not a JSON column: the project
supports both PostgreSQL (JSONB) and SQLite (used by the test suite), and a
plain text column behaves identically on both. The repository owns the
encode/decode, so no caller sees the string form.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiConversationModel(Base):
    """One assistant chat thread."""

    __tablename__ = "ai_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="Auto-generated from the first message"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class AiMessageModel(Base):
    """One transcript entry inside a conversation."""

    __tablename__ = "ai_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Ordering key within the conversation"
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="user | assistant | tool"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="JSON string: tool calls / tool_call_id"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        # The history read is always "this conversation, oldest first", so the
        # index covers both the filter and the sort.
        Index("ix_ai_messages_conversation_position", "conversation_id", "position"),
    )
