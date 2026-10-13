"""cloud workspaces: durable workspaces, runtimes, executions, environment snapshots

Creates the four tables that make a workspace a durable thing rather than a
container:

* ``workspaces`` — the logical resource the user owns.
* ``workspace_runtimes`` — the disposable compute instance currently attached to
  a workspace, at most one per workspace.
* ``executions`` — one submitted unit of work, durable and independent of any
  runtime.
* ``runtime_image_revisions`` — saved environment snapshots and whether they can
  still be reused.

Three decisions worth recording, because each one is a bug avoided:

1. **Lifecycle states are ``VARCHAR``, not PostgreSQL enums.** A PG enum has to
   be grown with ``ALTER TYPE``, and a later migration cannot use a value added
   in the same transaction (the ``0009``/``0010`` trap this repo already hit).
   It also outlives its table, so a re-created table collides with the orphaned
   type. The sets are small, internal and validated by the domain.
2. **One runtime per workspace is a database constraint** (``uq_workspace_runtimes_workspace_id``),
   not a service-level check. Two runtimes for one workspace is the "a second
   start allocated another instance" failure, and two concurrent starts is
   exactly how it happens.
3. **The idempotency key is a partial unique index.** A client-supplied key must
   identify one execution per workspace, while the (common) NULL keys must not
   collide with one another.

The whole migration is guarded and additive, so re-applying it is harmless and a
database that already has part of it converges rather than aborting startup.

Revision ID: 0028_cloud_workspaces
Revises: 0027_agent_folder_scope
Create Date: 2026-10-13
"""

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_cloud_workspaces"
down_revision: Union[str, Sequence[str], None] = "0027_agent_folder_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WORKSPACES = "workspaces"
_RUNTIMES = "workspace_runtimes"
_EXECUTIONS = "executions"
_REVISIONS = "runtime_image_revisions"

#: ``(index name, table, columns, unique, where)`` — created only when absent.
_INDEXES: tuple[tuple[str, str, tuple[str, ...], bool, str | None], ...] = (
    ("ix_workspaces_user_id", _WORKSPACES, ("user_id",), False, None),
    ("uq_workspace_runtimes_workspace_id", _RUNTIMES, ("workspace_id",), True, None),
    ("ix_executions_workspace_id", _EXECUTIONS, ("workspace_id",), False, None),
    ("ix_executions_user_id", _EXECUTIONS, ("user_id",), False, None),
    ("ix_executions_status", _EXECUTIONS, ("status",), False, None),
    (
        "ix_executions_workspace_id_created_at",
        _EXECUTIONS,
        ("workspace_id", "created_at"),
        False,
        None,
    ),
    (
        "uq_executions_workspace_idempotency",
        _EXECUTIONS,
        ("workspace_id", "idempotency_key"),
        True,
        "idempotency_key IS NOT NULL",
    ),
    ("ix_runtime_image_revisions_workspace_id", _REVISIONS, ("workspace_id",), False, None),
    (
        "ix_runtime_image_revisions_reuse",
        _REVISIONS,
        ("workspace_id", "status", "config_fingerprint"),
        False,
        None,
    ),
)


def _table_exists(connection, table: str) -> bool:
    return sa.inspect(connection).has_table(table)


def _index_exists(connection, table: str, index: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return index in {existing["name"] for existing in inspector.get_indexes(table)}


def upgrade() -> None:
    connection = op.get_bind()

    if not _table_exists(connection, _WORKSPACES):
        op.create_table(
            _WORKSPACES,
            sa.Column("workspace_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column(
                "description", sa.String(length=400), nullable=False, server_default=""
            ),
            sa.Column("runtime_config", sa.JSON(), nullable=False),
            sa.Column(
                "state",
                sa.String(length=20),
                nullable=False,
                server_default="ACTIVE",
                comment="Workspace lifecycle state: ACTIVE | ARCHIVED | DELETING",
            ),
            sa.Column(
                "execution_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists(connection, _RUNTIMES):
        op.create_table(
            _RUNTIMES,
            sa.Column("runtime_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey(f"{_WORKSPACES}.workspace_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("provider_ref", sa.String(length=120), nullable=True),
            sa.Column(
                "state",
                sa.String(length=20),
                nullable=False,
                server_default="STOPPED",
                comment=(
                    "Runtime lifecycle state: STOPPED | STARTING | RUNNING "
                    "| STOPPING | FAILED"
                ),
            ),
            sa.Column("image_revision_id", sa.String(length=36), nullable=True),
            sa.Column(
                "resource_profile",
                sa.String(length=32),
                nullable=False,
                server_default="standard",
            ),
            sa.Column("failure_reason", sa.String(length=500), nullable=True),
            sa.Column(
                "provider_metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists(connection, _EXECUTIONS):
        op.create_table(
            _EXECUTIONS,
            sa.Column("execution_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey(f"{_WORKSPACES}.workspace_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("mode", sa.String(length=16), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="QUEUED",
                comment=(
                    "QUEUED | RUNNING | SUCCEEDED | FAILED | CANCELLED | TIMED_OUT"
                ),
            ),
            sa.Column("runtime_id", sa.String(length=36), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column(
                "stdout_excerpt", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column(
                "stderr_excerpt", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column("stdout_ref", sa.String(length=400), nullable=True),
            sa.Column("stderr_ref", sa.String(length=400), nullable=True),
            sa.Column(
                "stdout_truncated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "stderr_truncated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("error", sa.String(length=1000), nullable=True),
            sa.Column(
                "artifacts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
            ),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "memory_peak_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("idempotency_key", sa.String(length=120), nullable=True),
            sa.Column(
                "origin",
                sa.String(length=16),
                nullable=False,
                server_default="WEB",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists(connection, _REVISIONS):
        op.create_table(
            _REVISIONS,
            sa.Column("revision_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=36),
                sa.ForeignKey(f"{_WORKSPACES}.workspace_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("base_image_revision", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="PENDING",
                comment="PENDING | BUILDING | READY | FAILED",
            ),
            sa.Column(
                "provider_snapshot_ref", sa.String(length=200), nullable=True
            ),
            sa.Column("error", sa.String(length=1000), nullable=True),
            sa.Column(
                "validation_metadata",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        )

    for name, table, columns, unique, where in _INDEXES:
        if _index_exists(connection, table, name):
            continue
        # ``dict[str, Any]`` on purpose: the partial-index argument is a
        # backend-specific kwarg that Alembic's typed signature does not name, so
        # an untyped unpack would be reported as a type error.
        kwargs: dict[str, Any] = {}
        if where is not None:
            # A partial unique index is the whole point of the idempotency key:
            # NULL keys must not collide with each other.
            kwargs["postgresql_where"] = sa.text(where)
        op.create_index(name, table, list(columns), unique=unique, **kwargs)


def downgrade() -> None:
    """Drop the four tables.

    The workspace's durable *files* are provider-side and are NOT touched by this
    migration — dropping these rows makes the workspaces unreachable through
    Transform, which is the honest consequence of a downgrade. The provider
    resources are removed by the cleanup policy, not by Alembic.
    """
    for name, table, _columns, _unique, _where in reversed(_INDEXES):
        if _index_exists(op.get_bind(), table, name):
            op.drop_index(name, table_name=table)
    for table in (_REVISIONS, _EXECUTIONS, _RUNTIMES, _WORKSPACES):
        if _table_exists(op.get_bind(), table):
            op.drop_table(table)
