"""add is_favorite flag to user_files

Revision ID: 0008_add_favorite_flag
Revises: 0007_add_object_key
Create Date: 2026-08-22 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008_add_favorite_flag'
down_revision: Union[str, Sequence[str], None] = '0007_add_object_key'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the is_favorite boolean flag (default false) to user_files."""
    op.add_column(
        'user_files',
        sa.Column('is_favorite', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.create_index('ix_user_files_is_favorite', 'user_files', ['is_favorite'])


def downgrade() -> None:
    """Remove the is_favorite column and its index."""
    op.drop_index('ix_user_files_is_favorite', table_name='user_files')
    op.drop_column('user_files', 'is_favorite')
