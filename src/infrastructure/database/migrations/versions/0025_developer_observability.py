"""developer observability: API request logs, metrics and MCP tool activity

Adds the persistence behind the Developer section of the app:

* ``api_request_events`` — one row per captured public API request. Powers the
  API Logs table, the time-series chart and every summary metric.
* ``mcp_tool_invocations`` — one row per MCP tool call made by an authorized AI
  agent. Powers the MCP Activity log.
* ``mcp_agent_grants.paused_at`` — the audit timestamp beside the grant's
  ``status``, set when a user pauses a connection and cleared on resume.

WHY request events get their own table rather than reusing an access log or the
platform's Prometheus counters: the product requirement is *per-account
attribution* ("what did MY key do?"), which Prometheus label cardinality cannot
serve and a process log cannot scope by owner. A row per request with an
``account_id`` is the smallest shape that answers the question and stays
queryable with ordinary indexes.

Column types are chosen so this migration cannot fail the way the enum ones can:
no new PostgreSQL enum is created (the ``outcome`` column is a ``VARCHAR``), and
every statement is guarded, so re-running against a database that already has
the tables is a no-op instead of aborting startup.

Retention is NOT implemented as a partition here. These rows are per-account
request metadata — orders of magnitude below the volume that justifies
partitioned tables and their operational weight — so a periodic indexed delete
(after the configurable retention window) is both sufficient and simpler to
reason about.

Revision ID: 0025_developer_observability
Revises: 0024_mcp_oauth
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0025_developer_observability"
down_revision: Union[str, Sequence[str], None] = "0024_mcp_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(connection, table: str) -> bool:
    return sa.inspect(connection).has_table(table)


def _column_exists(connection, table: str, column: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    connection = op.get_bind()

    if not _table_exists(connection, "api_request_events"):
        op.create_table(
            "api_request_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "account_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "api_key_id",
                sa.String(64),
                sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("request_id", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("method", sa.String(10), nullable=False),
            sa.Column("route_template", sa.String(255), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Float(), nullable=False),
            sa.Column("environment", sa.String(20), nullable=True),
            sa.Column("request_bytes", sa.Integer(), nullable=True),
            sa.Column("response_bytes", sa.Integer(), nullable=True),
        )
        # One index per query shape the dashboard runs; not one per column.
        op.create_index(
            "ix_api_request_events_account_time",
            "api_request_events",
            ["account_id", "created_at"],
        )
        op.create_index(
            "ix_api_request_events_account_key_time",
            "api_request_events",
            ["account_id", "api_key_id", "created_at"],
        )
        op.create_index(
            "ix_api_request_events_account_status_time",
            "api_request_events",
            ["account_id", "status_code", "created_at"],
        )
        op.create_index(
            "ix_api_request_events_request_id",
            "api_request_events",
            ["request_id"],
        )

    if not _table_exists(connection, "mcp_tool_invocations"):
        op.create_table(
            "mcp_tool_invocations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "account_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("grant_id", sa.String(64), nullable=False),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("tool_name", sa.String(100), nullable=False),
            sa.Column("outcome", sa.String(16), nullable=False),
            sa.Column("error_category", sa.String(50), nullable=True),
            sa.Column("duration_ms", sa.Float(), nullable=True),
            sa.Column("request_id", sa.String(80), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_mcp_tool_invocations_account_time",
            "mcp_tool_invocations",
            ["account_id", "created_at"],
        )
        op.create_index(
            "ix_mcp_tool_invocations_account_grant_time",
            "mcp_tool_invocations",
            ["account_id", "grant_id", "created_at"],
        )
        op.create_index(
            "ix_mcp_tool_invocations_account_tool",
            "mcp_tool_invocations",
            ["account_id", "tool_name"],
        )
        op.create_index(
            "ix_mcp_tool_invocations_account_outcome",
            "mcp_tool_invocations",
            ["account_id", "outcome", "created_at"],
        )

    # The pause control's audit timestamp. The authoritative state is the
    # existing ``status`` VARCHAR, which already accepts 'PAUSED' — this column
    # only records *when*, so no enum change is needed anywhere.
    if not _column_exists(connection, "mcp_agent_grants", "paused_at"):
        op.add_column(
            "mcp_agent_grants",
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    connection = op.get_bind()
    if _column_exists(connection, "mcp_agent_grants", "paused_at"):
        op.drop_column("mcp_agent_grants", "paused_at")
    for table in ("mcp_tool_invocations", "api_request_events"):
        if _table_exists(connection, table):
            op.drop_table(table)
