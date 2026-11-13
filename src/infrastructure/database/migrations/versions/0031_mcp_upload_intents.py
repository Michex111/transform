"""MCP upload intents: a single-use capability to receive one attached file.

An MCP client's chat attachment never reaches this server: the model sees
extracted text and images, not the original bytes, so it cannot rebuild a PDF or
a DOCX. ``mcp_upload_intents`` is the missing byte path — the agent mints an
intent plus a short-lived capability, and the client's sandbox (or a browser
page) uploads the real file to it.

The table stores only *what was authorized* at creation time: the grant, the
destination folder, the size ceiling and the optional conversion target. A
redemption re-checks those against the grant as it is now, so revoking, pausing,
narrowing or re-binding the connection all take effect on the next attempt. No
capability, token, storage key or bucket is ever persisted here.

``status`` is a plain ``VARCHAR`` for the same reason as the MCP OAuth tables:
a PostgreSQL enum type outlives its tables, and an inline ``sa.Enum`` would make
a re-run of ``upgrade head`` fail with ``DuplicateObjectError``.

Revision ID: 0031_mcp_upload_intents
Revises: 0030_mcp_authorization_core
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_mcp_upload_intents"
down_revision: Union[str, Sequence[str], None] = "0030_mcp_authorization_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mcp_upload_intents"
_USER_CREATED_INDEX = "ix_mcp_upload_intents_user_created"
_EXPIRES_INDEX = "ix_mcp_upload_intents_expires_at"
_GRANT_INDEX = "ix_mcp_upload_intents_grant_id"


def _has_table() -> bool:
    inspector = sa.inspect(op.get_bind())
    return _TABLE in inspector.get_table_names()


def _has_index(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in {index["name"] for index in inspector.get_indexes(_TABLE)}


def upgrade() -> None:
    # Guarded because ``RUN_MIGRATIONS=true`` turns a migration error into a
    # startup failure: a database where the table was created out of band must
    # converge, not abort the boot.
    if not _has_table():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("grant_id", sa.String(length=64), nullable=False),
            sa.Column("client_id", sa.String(length=255), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("extension", sa.String(length=32), nullable=False),
            sa.Column("max_bytes", sa.Integer(), nullable=False),
            sa.Column("folder_id", sa.String(length=64), nullable=True),
            sa.Column("target_format", sa.String(length=32), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("file_id", sa.String(length=64), nullable=True),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_index(_GRANT_INDEX):
        op.create_index(_GRANT_INDEX, _TABLE, ["grant_id"])
    if not _has_index(_USER_CREATED_INDEX):
        op.create_index(_USER_CREATED_INDEX, _TABLE, ["user_id", "created_at"])
    if not _has_index(_EXPIRES_INDEX):
        op.create_index(_EXPIRES_INDEX, _TABLE, ["expires_at"])


def downgrade() -> None:
    if not _has_table():
        return
    for name in (_USER_CREATED_INDEX, _EXPIRES_INDEX, _GRANT_INDEX):
        if _has_index(name):
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
