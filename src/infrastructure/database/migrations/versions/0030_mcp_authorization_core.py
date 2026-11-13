"""MCP authorization core: refresh-rotation attribution and per-grant jobs

Two additive, nullable columns. Both are about making an existing mechanism
*attributable* rather than about adding a new capability:

* ``mcp_oauth_tokens.replaced_by`` — the hash of the token a refresh rotation
  replaced this one with. ``revoked_at`` alone cannot distinguish "the user
  revoked this" from "a refresh rotated it", and the reuse grace window must
  only ever apply to the second. Nullable, so every existing token reads as
  "not rotated", which is the conservative interpretation (no grace).
* ``conversion_jobs.grant_id`` — the ``mcp_agent_grants`` row that started an
  agent-created conversion. Nullable because the overwhelming majority of jobs
  were not started by an agent. With it, ``get_conversion_history`` with the
  ``AGENT`` history scope can mean "conversions *this* connection started"
  instead of "any connected application of this user".

Neither column is backfilled: there is no historical information to backfill
from, and a guessed value would be worse than a NULL. Both are added with an
``IF NOT EXISTS`` guard so a partially-applied environment converges.

Revision ID: 0030_mcp_authorization_core
Revises: 0029_payment_grace
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_mcp_authorization_core"
down_revision: Union[str, Sequence[str], None] = "0029_payment_grace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOKENS_TABLE = "mcp_oauth_tokens"
_JOBS_TABLE = "conversion_jobs"
_INDEX_NAME = "ix_conversion_jobs_grant_id"


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column(_TOKENS_TABLE, "replaced_by"):
        op.add_column(
            _TOKENS_TABLE,
            sa.Column("replaced_by", sa.String(length=64), nullable=True),
        )
    if not _has_column(_JOBS_TABLE, "grant_id"):
        op.add_column(
            _JOBS_TABLE,
            sa.Column("grant_id", sa.String(length=64), nullable=True),
        )
        op.create_index(_INDEX_NAME, _JOBS_TABLE, ["grant_id"])


def downgrade() -> None:
    if _has_column(_JOBS_TABLE, "grant_id"):
        op.drop_index(_INDEX_NAME, table_name=_JOBS_TABLE)
        op.drop_column(_JOBS_TABLE, "grant_id")
    if _has_column(_TOKENS_TABLE, "replaced_by"):
        op.drop_column(_TOKENS_TABLE, "replaced_by")
