"""Workspace, runtime, execution and environment-snapshot ORM models.

Lifecycle states are stored as ``String`` columns rather than PostgreSQL enums.
That is deliberate and it follows the ``mcp`` tables' precedent: a PG enum needs
``ALTER TYPE ... ADD VALUE`` to grow, values added in the same transaction cannot
be used by a later migration (a bug this repository has already been burned by —
see the ``0009``/``0010`` pair and ``transaction_per_migration``), and an enum
type is not dropped with its table, so a re-created table collides with the
orphaned type. These are small, internal, closed sets validated by the domain on
the way in; the database does not need to be the thing that learns about them.

``provider_metadata``, ``runtime_config``, ``artifacts`` and
``validation_metadata`` are ``JSON`` because they are always read and written
whole and nothing queries inside them — the same reasoning ``saved_workflows``
records for its ``definition`` column.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class WorkspaceModel(Base):
    """The durable workspace. Owns nothing provider-specific."""

    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(
        String(400), nullable=False, default="", server_default=""
    )
    #: ``RuntimeConfig.to_dict()``. A JSON column rather than a column per knob
    #: because the configuration is versioned as a whole (`config_version`) and
    #: compared by fingerprint, not field by field.
    runtime_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
        comment="Workspace lifecycle state: ACTIVE | ARCHIVED | DELETING",
    )
    #: Denormalised count maintained on submit, so the list view does not need an
    #: aggregate query per row.
    execution_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    #: Distinct from ``updated_at``: a rename moves ``updated_at`` but is not
    #: "the last time you used this workspace".
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkspaceRuntimeModel(Base):
    """The single runtime attached to a workspace (at most one per workspace)."""

    __tablename__ = "workspace_runtimes"

    runtime_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Opaque handle into the provider (a container id). Never a public id.
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="STOPPED",
        server_default="STOPPED",
        comment="Runtime lifecycle state: STOPPED | STARTING | RUNNING | STOPPING | FAILED",
    )
    image_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard", server_default="standard"
    )
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_metadata: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One runtime per workspace. Enforced by the database rather than
        # trusted to the service, because two runtimes for one workspace is
        # exactly the "start created a second instance" failure the brief calls
        # out, and a race between two concurrent starts is the way it happens.
        UniqueConstraint(
            "workspace_id", name="uq_workspace_runtimes_workspace_id"
        ),
    )


class ExecutionModel(Base):
    """One submitted unit of work. Durable, and independent of any runtime."""

    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="SCRIPT | COMMAND | PROJECT"
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="QUEUED",
        server_default="QUEUED",
        index=True,
        comment="QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED | TIMED_OUT",
    )
    runtime_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Bounded inline capture; the full stream lives at ``stdout_ref`` when it
    #: exceeded the bound.
    stdout_excerpt: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    stderr_excerpt: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    stdout_ref: Mapped[str | None] = mapped_column(String(400), nullable=True)
    stderr_ref: Mapped[str | None] = mapped_column(String(400), nullable=True)
    stdout_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    stderr_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    artifacts: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default=sa.text("'[]'")
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    memory_peak_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="WEB", server_default="WEB"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Partial unique index: a client-supplied idempotency key identifies one
        # execution per workspace, and NULL keys (the common case) must not
        # collide with each other — which is exactly what a full unique index
        # would do, since SQL treats NULLs as distinct in only *some* engines.
        Index(
            "uq_executions_workspace_idempotency",
            "workspace_id",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
            sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_executions_workspace_id_created_at", "workspace_id", "created_at"),
    )


class RuntimeImageRevisionModel(Base):
    """A saved, reusable representation of a workspace's environment."""

    __tablename__ = "runtime_image_revisions"

    revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Base image revision + Python + uv + config version + resource profile.
    #: 64 hex characters (SHA-256). Deliberately NOT the dependency lockfile:
    #: see the entity's module docstring for why the lockfile is reconciled after
    #: the runtime starts instead.
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    base_image_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
        comment="PENDING | BUILDING | READY | FAILED",
    )
    provider_snapshot_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    validation_metadata: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Backs ``find_reusable_revision``: the reuse query filters on exactly
        # these three columns.
        Index(
            "ix_runtime_image_revisions_reuse",
            "workspace_id",
            "status",
            "config_fingerprint",
        ),
    )
