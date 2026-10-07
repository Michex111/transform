"""add progress percentage to conversion_jobs

Records how far the worker has got on a job, as a percentage
(0/25/50/75/100), so the API can return it and the SPA can draw a real progress
bar the moment a client reconnects or reloads a historical chat.

Before this column the percentage existed only inside the Redis event stream:
a client that opened or reloaded a chat had to wait for the SSE stream to replay
the job's events before it could show anything other than an indeterminate
sweep — and if that stream never delivered (a dropped connection, or a
subscribed-after-completion window) it showed no real progress at all.

Default 0 means "not started or not reported", which the SPA renders as an
indeterminate bar rather than as an empty 0%. Existing rows are left at 0: a
finished job's progress is derivable from its status, and a historical job's
mid-flight percentage was never observed and is not recoverable.

Revision ID: 0023_add_job_progress
Revises: 0022_backfill_purchased_credits
Create Date: 2026-10-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023_add_job_progress"
down_revision: Union[str, Sequence[str], None] = "0022_backfill_purchased_credits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "conversion_jobs"
_COLUMN = "progress"


def _column_exists(connection, table: str, column: str) -> bool:
    """True when ``table.column`` already exists."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the ``progress`` column, tolerating an already-applied run.

    Re-runnable for the same reason ``0014_add_job_file_sizes`` is: alembic's
    version table is the only record of what has been applied, and a migration
    applied out of band would otherwise abort startup with a duplicate-column
    error. The column is additive with a server default, so re-running is
    harmless.
    """
    connection = op.get_bind()
    if _column_exists(connection, _TABLE, _COLUMN):
        return

    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Worker progress percentage (0/25/50/75/100); 0 = not reported",
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if not _column_exists(connection, _TABLE, _COLUMN):
        return
    op.drop_column(_TABLE, _COLUMN)
