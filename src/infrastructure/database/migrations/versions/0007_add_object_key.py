"""add object_key column to conversion_jobs

Revision ID: 0007_add_object_key
Revises: 0006_user_folders
Create Date: 2026-08-21 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007_add_object_key'
down_revision: Union[str, Sequence[str], None] = '0006_user_folders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the object_key column (name of the file as stored on object storage)."""
    op.add_column(
        'conversion_jobs',
        sa.Column('object_key', sa.String(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    """Remove the object_key column."""
    op.drop_column('conversion_jobs', 'object_key')
