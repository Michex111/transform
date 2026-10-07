"""add MCP (agent access) OAuth tables

Creates the four tables behind **remote MCP** — the interface that lets an
authorized AI agent act on a user's documents:

* ``mcp_oauth_clients`` — dynamically registered agent applications.
* ``mcp_agent_grants``  — a user's standing consent for one client. This is the
  revocable unit shown on the "Connected apps" settings screen.
* ``mcp_oauth_codes``   — single-use PKCE authorization codes.
* ``mcp_oauth_tokens``  — access and refresh tokens, stored as SHA-256 hashes.

WHY these tables exist rather than reusing ``api_keys``: an API key is a
long-lived, all-powerful credential the user pastes into a tool. MCP access is
granted to a *third-party application* through a browser consent flow, is
scoped to a subset of capabilities, and must be revocable per-application
without touching the user's password or their API keys. Those are different
lifecycles and conflating them would mean an agent either gets more authority
than it needs or cannot be revoked independently.

Columns holding credentials (codes, token values) store only a SHA-256 hash;
the plaintext never reaches the database. ``status``/``kind`` are plain
``VARCHAR`` rather than PostgreSQL enum types on purpose — a new enum type must
be created with ``checkfirst=True`` + ``create_type=False`` or ``upgrade head``
dies with ``DuplicateObjectError`` on a database whose type outlived its table
(see ``test_migration_idempotency.py``), and the values are only ever read
through the domain enum anyway.

The whole migration is idempotent: each ``create_table`` is skipped when the
table already exists, so an out-of-band application cannot abort startup with
``RUN_MIGRATIONS=true``.

Revision ID: 0024_mcp_oauth
Revises: 0023_add_job_progress
Create Date: 2026-10-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0024_mcp_oauth"
down_revision: Union[str, Sequence[str], None] = "0023_add_job_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "mcp_oauth_clients",
    "mcp_agent_grants",
    "mcp_oauth_codes",
    "mcp_oauth_tokens",
)


def _table_exists(connection, table: str) -> bool:
    return sa.inspect(connection).has_table(table)


def upgrade() -> None:
    connection = op.get_bind()

    if not _table_exists(connection, "mcp_oauth_clients"):
        op.create_table(
            "mcp_oauth_clients",
            sa.Column("client_id", sa.String(255), primary_key=True),
            sa.Column("client_secret", sa.String(255), nullable=True),
            sa.Column("client_name", sa.String(200), nullable=True),
            sa.Column("redirect_uris", sa.JSON(), nullable=False),
            sa.Column("grant_types", sa.JSON(), nullable=False),
            sa.Column("response_types", sa.JSON(), nullable=False),
            sa.Column("scope", sa.Text(), nullable=True),
            sa.Column("token_endpoint_auth_method", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not _table_exists(connection, "mcp_agent_grants"):
        op.create_table(
            "mcp_agent_grants",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("client_name", sa.String(200), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("resource", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "client_id", name="uq_mcp_grant_user_client"),
        )
        op.create_index("ix_mcp_agent_grants_user_id", "mcp_agent_grants", ["user_id"])
        op.create_index("ix_mcp_agent_grants_client_id", "mcp_agent_grants", ["client_id"])

    if not _table_exists(connection, "mcp_oauth_codes"):
        op.create_table(
            "mcp_oauth_codes",
            sa.Column("code_hash", sa.String(64), primary_key=True),
            sa.Column("grant_id", sa.String(64), nullable=False),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("subject", sa.String(64), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("code_challenge", sa.Text(), nullable=False),
            sa.Column("redirect_uri", sa.Text(), nullable=False),
            sa.Column("resource", sa.String(500), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_mcp_oauth_codes_grant_id", "mcp_oauth_codes", ["grant_id"])

    if not _table_exists(connection, "mcp_oauth_tokens"):
        op.create_table(
            "mcp_oauth_tokens",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("grant_id", sa.String(64), nullable=False),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("subject", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("resource", sa.String(500), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_mcp_oauth_tokens_grant_id", "mcp_oauth_tokens", ["grant_id"])


def downgrade() -> None:
    connection = op.get_bind()
    for table in reversed(_TABLES):
        if _table_exists(connection, table):
            op.drop_table(table)
