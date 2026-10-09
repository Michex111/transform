"""ORM models for the Developer observability feature.

Two append-only tables:

* ``api_request_events`` — one row per captured public API request, owned by an
  account and (when applicable) attributed to one API key.
* ``mcp_tool_invocations`` — one row per MCP tool call made by an authorized AI
  agent, owned by an account and attributed to one consent grant.

Design notes that matter beyond style:

* **Account scoping is a column, not a join.** ``account_id`` is denormalised
  onto both tables and every read filters on it. There is no dashboard query
  that does not start with ``WHERE account_id = :me``, which is what makes one
  user's activity unreachable to another.
* **Nothing sensitive has a column.** No URL, no body, no header, no argument
  blob. ``route_template`` stores the matched route *pattern*
  (``/api/v1/files/{file_id}``), so ids never reach the table and "requests by
  endpoint" stays a finite grouping.
* **Indexes follow the queries.** Every one of the four indexes on
  ``api_request_events`` is the leading edge of a query the dashboard actually
  runs (the log list, the per-key filter, the status filter, and a request-id
  lookup); nothing is indexed speculatively, because each index also costs a
  write on the ingestion path.
* Retention is enforced by a periodic delete (see the cleanup worker), not by a
  partitioned table — the volume here is per-account request metadata, far below
  what would justify the operational weight of partitioning.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class ApiRequestEventModel(Base):
    """One captured API request, attributed to an account and optional key."""

    __tablename__ = "api_request_events"
    __table_args__ = (
        # The log list and every time-bucketed metric query: account first (the
        # isolation predicate, always present) then the time range, so an index
        # range scan serves both the filter and the ordering.
        Index("ix_api_request_events_account_time", "account_id", "created_at"),
        # Per-key attribution ("what did this key do?").
        Index("ix_api_request_events_account_key_time", "account_id", "api_key_id", "created_at"),
        # Success/error filtering and the status distribution.
        Index("ix_api_request_events_account_status_time", "account_id", "status_code", "created_at"),
        # Correlating a support ticket's request id back to a row.
        Index("ix_api_request_events_request_id", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: Nullable because a request authenticated with the SPA's session JWT has
    #: no key. SET NULL (rather than CASCADE) on key deletion: removing a key
    #: must not erase the history of what it did.
    api_key_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    route_template: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Float, not Integer: sub-millisecond durations are real for in-process
    #: routes and rounding them to 0 would make the p50 of a fast endpoint
    #: look like a timeout-free zero rather than "fast".
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    environment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    request_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class McpToolInvocationModel(Base):
    """One MCP tool call made by an authorized agent application."""

    __tablename__ = "mcp_tool_invocations"
    __table_args__ = (
        Index("ix_mcp_tool_invocations_account_time", "account_id", "created_at"),
        # "What has this connection been doing?" — the per-grant drill-down.
        Index("ix_mcp_tool_invocations_account_grant_time", "account_id", "grant_id", "created_at"),
        # Tool breakdown and the tool filter.
        Index("ix_mcp_tool_invocations_account_tool", "account_id", "tool_name"),
        # Outcome filter (success / error / denied).
        Index("ix_mcp_tool_invocations_account_outcome", "account_id", "outcome", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The consent grant that authorized the call. This is the trustworthy
    #: identity — never a name the agent supplied.
    grant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
