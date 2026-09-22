"""Create conversion jobs table.

Revision ID: 0001_create_conversion_jobs
Revises:
Create Date: 2026-07-12 07:40:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001_create_conversion_jobs"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_JOBSTATUS_VALUES = ("PENDING", "PROCESSING", "COMPLETED", "FAILED")


def _jobstatus_enum(create_type: bool = True) -> postgresql.ENUM:
    """The ``jobstatus`` enum, created explicitly rather than implicitly.

    ``create_type=False`` is used for the column reference and the type is
    created separately with ``checkfirst=True``, because ``sa.Enum`` inside
    ``create_table`` emits an unguarded ``CREATE TYPE``. That raises
    ``DuplicateObjectError`` on any database that already has the type — which
    happens whenever a schema is rebuilt from an empty database whose enum
    types outlived a ``DROP TABLE`` (PostgreSQL does not drop a type with the
    table that happened to use it), so the whole ``upgrade head`` would abort
    and the API would refuse to boot. Same pattern as ``0003``/``0009``.
    """
    return postgresql.ENUM(*_JOBSTATUS_VALUES, name="jobstatus", create_type=create_type)


def upgrade() -> None:
    jobstatus = _jobstatus_enum(create_type=False)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _jobstatus_enum(create_type=True).create(bind, checkfirst=True)

    op.create_table(
        "conversion_jobs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("status", jobstatus, nullable=False),
        sa.Column("source_format", sa.String(), nullable=False),
        sa.Column("target_format", sa.String(), nullable=False),
        sa.Column("input_file", sa.String(), nullable=False),
        sa.Column("output_file", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("conversion_jobs")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS jobstatus")
