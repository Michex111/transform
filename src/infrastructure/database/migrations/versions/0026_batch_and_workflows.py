"""batch and workflow grouping columns, plus saved_workflows

Adds the three pieces the batch-conversion and saved-workflow features need:

* ``conversion_jobs.batch_id`` — groups the jobs one batch request created.
* ``conversion_jobs.workflow_id`` — records which saved workflow produced a job.
* ``saved_workflows`` — the stored, validated workflow definitions.

There is deliberately **no** parent ``batches`` table. The jobs are already
persisted, already carry their own status, and are already streamed
individually, so a parent row would hold a second copy of state that could drift
out of step with its children. A batch is a query over ``batch_id``, and an
aggregate is computed from the children rather than stored beside them.

Both new columns are nullable with no server default, which is what makes this
migration cheap on a populated table: every existing job is legitimately "not
part of a batch" and "not produced by a workflow", so NULL is the correct
backfill rather than a value that has to be written.

``workflow_id`` is intentionally *not* a foreign key. A hard FK would force
deleting a workflow to either fail or cascade into the user's conversion
history, and that history is the user's own record of real work, which must
outlive the shortcut that produced it.

Revision ID: 0026_batch_and_workflows
Revises: 0025_developer_observability
Create Date: 2026-10-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0026_batch_and_workflows"
down_revision: Union[str, Sequence[str], None] = "0025_developer_observability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JOBS_TABLE = "conversion_jobs"
_WORKFLOWS_TABLE = "saved_workflows"

#: ``column name -> index name`` for the two grouping columns.
_JOB_COLUMNS = {
    "batch_id": "ix_conversion_jobs_batch_id",
    "workflow_id": "ix_conversion_jobs_workflow_id",
}


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def _index_exists(connection, table: str, index: str) -> bool:
    """True when ``table`` already carries an index called ``index``."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return index in {existing["name"] for existing in inspector.get_indexes(table)}


def _table_exists(connection, table: str) -> bool:
    return sa.inspect(connection).has_table(table)


def upgrade() -> None:
    """Add the grouping columns and create ``saved_workflows``.

    Every step is guarded, for the same reason ``0023_add_job_progress`` is:
    alembic's version table is the only record of what has been applied, so a
    migration applied out of band would otherwise abort startup with a
    duplicate-column or duplicate-table error. All three steps are additive, so
    re-running is harmless.
    """
    connection = op.get_bind()

    for column, index in _JOB_COLUMNS.items():
        if not _column_exists(connection, _JOBS_TABLE, column):
            op.add_column(
                _JOBS_TABLE,
                sa.Column(
                    column,
                    sa.String(length=36),
                    nullable=True,
                    comment=(
                        "Batch that created this job (NULL when not part of one)"
                        if column == "batch_id"
                        else "Saved workflow that produced this job (NULL when not from one)"
                    ),
                ),
            )
        if not _index_exists(connection, _JOBS_TABLE, index):
            op.create_index(index, _JOBS_TABLE, [column])

    if not _table_exists(connection, _WORKFLOWS_TABLE):
        op.create_table(
            _WORKFLOWS_TABLE,
            sa.Column("workflow_id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column(
                "description",
                sa.String(length=400),
                nullable=False,
                server_default="",
            ),
            sa.Column("definition", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_saved_workflows_user_id", _WORKFLOWS_TABLE, ["user_id"]
        )


def downgrade() -> None:
    connection = op.get_bind()

    if _table_exists(connection, _WORKFLOWS_TABLE):
        if _index_exists(connection, _WORKFLOWS_TABLE, "ix_saved_workflows_user_id"):
            op.drop_index("ix_saved_workflows_user_id", table_name=_WORKFLOWS_TABLE)
        op.drop_table(_WORKFLOWS_TABLE)

    for column, index in _JOB_COLUMNS.items():
        if _index_exists(connection, _JOBS_TABLE, index):
            op.drop_index(index, table_name=_JOBS_TABLE)
        if _column_exists(connection, _JOBS_TABLE, column):
            op.drop_column(_JOBS_TABLE, column)
