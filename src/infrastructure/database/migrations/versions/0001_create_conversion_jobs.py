"""Create conversion jobs table.

Revision ID: 0001_create_conversion_jobs
Revises:
Create Date: 2026-07-12 07:40:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_create_conversion_jobs"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversion_jobs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PROCESSING", "COMPLETED", "FAILED", name="jobstatus"),
            nullable=False,
        ),
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
