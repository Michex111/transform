"""add AI assistant conversations and messages

Creates the storage behind the "Transform AI" assistant:

* ``ai_conversations`` — one row per chat thread, owned by a user. ``user_id``
  cascades on delete so closing an account never leaves a readable transcript
  behind.
* ``ai_messages`` — the ordered transcript. ``conversation_id`` cascades, and
  the ``(conversation_id, position)`` index covers the only read pattern there
  is ("this conversation, oldest first").

Both tables are new, so there is nothing to backfill and no existing row is
touched. The upgrade is **idempotent** — see :func:`upgrade` for why that
matters — and :func:`downgrade` drops messages before conversations for the same
reason the foreign key exists: dropping the parent first would either fail or
orphan rows.

Revision ID: 0019_ai_assistant
Revises: 0018_user_default_save_folder
Create Date: 2026-09-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0019_ai_assistant"
down_revision: Union[str, Sequence[str], None] = "0018_user_default_save_folder"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONVERSATIONS = "ai_conversations"
_MESSAGES = "ai_messages"
_CONVERSATION_USER_INDEX = "ix_ai_conversations_user_id"
_MESSAGE_CONVERSATION_INDEX = "ix_ai_messages_conversation_id"
_MESSAGE_POSITION_INDEX = "ix_ai_messages_conversation_position"


def _has_table(connection, table: str) -> bool:
    """True when ``table`` already exists."""
    return sa.inspect(connection).has_table(table)


def _has_index(connection, table: str, index: str) -> bool:
    """True when ``index`` already exists on ``table``."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return index in {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    """Create both assistant tables, tolerating an already-applied run.

    Re-runnable for the same reason ``0013``/``0014``/``0018`` are: the alembic
    version table is the only record of what has been applied, and
    ``RUN_MIGRATIONS=true`` makes a migration error a *startup* failure (a total
    outage). A database where these tables were created out of band, or whose
    stamp was reset, must not abort the boot on a duplicate-table error.
    """
    connection = op.get_bind()

    if not _has_table(connection, _CONVERSATIONS):
        op.create_table(
            _CONVERSATIONS,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _has_index(connection, _CONVERSATIONS, _CONVERSATION_USER_INDEX):
        op.create_index(_CONVERSATION_USER_INDEX, _CONVERSATIONS, ["user_id"])

    if not _has_table(connection, _MESSAGES):
        op.create_table(
            _MESSAGES,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "conversation_id",
                sa.String(),
                sa.ForeignKey(f"{_CONVERSATIONS}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("tool_name", sa.String(length=64), nullable=True),
            sa.Column("meta", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _has_index(connection, _MESSAGES, _MESSAGE_CONVERSATION_INDEX):
        op.create_index(_MESSAGE_CONVERSATION_INDEX, _MESSAGES, ["conversation_id"])
    if not _has_index(connection, _MESSAGES, _MESSAGE_POSITION_INDEX):
        op.create_index(
            _MESSAGE_POSITION_INDEX, _MESSAGES, ["conversation_id", "position"]
        )


def downgrade() -> None:
    """Drop the messages table first, then the conversations it points at."""
    connection = op.get_bind()
    if _has_table(connection, _MESSAGES):
        op.drop_table(_MESSAGES)
    if _has_table(connection, _CONVERSATIONS):
        op.drop_table(_CONVERSATIONS)
