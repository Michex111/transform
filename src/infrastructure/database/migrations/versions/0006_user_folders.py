"""add user_folders table and folder_id to user_files

Revision ID: 0006_user_folders
Revises: 0005_credits_and_job_fields
Create Date: 2026-08-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006_user_folders'
down_revision: Union[str, Sequence[str], None] = '0005_credits_and_job_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_folders',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.String(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['user_folders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'parent_id', 'name', name='uq_user_folders_user_parent_name'
        ),
    )
    op.create_index('ix_user_folders_user_id', 'user_folders', ['user_id'])
    op.create_index('ix_user_folders_parent_id', 'user_folders', ['parent_id'])

    op.add_column(
        'user_files',
        sa.Column(
            'folder_id',
            sa.String(),
            nullable=True,
            comment='Parent folder, or NULL for root-level files',
        ),
    )
    op.create_foreign_key(
        'fk_user_files_folder_id_user_folders',
        'user_files',
        'user_folders',
        ['folder_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_user_files_folder_id', 'user_files', ['folder_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_user_files_folder_id', table_name='user_files')
    op.drop_constraint('fk_user_files_folder_id_user_folders', 'user_files', type_='foreignkey')
    op.drop_column('user_files', 'folder_id')

    op.drop_index('ix_user_folders_parent_id', table_name='user_folders')
    op.drop_index('ix_user_folders_user_id', table_name='user_folders')
    op.drop_table('user_folders')
