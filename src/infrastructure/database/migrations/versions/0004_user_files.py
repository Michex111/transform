"""add user_files table

Revision ID: 0004_user_files
Revises: a23fe025bb21
Create Date: 2026-08-09 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_user_files'
down_revision: Union[str, Sequence[str], None] = 'a23fe025bb21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('file_key', sa.String(512), nullable=False, comment='S3 object key'),
        sa.Column('file_name', sa.String(255), nullable=False, comment='Original filename'),
        sa.Column('file_size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('mime_type', sa.String(100), nullable=False, server_default='application/octet-stream'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True,
                  comment='When this file should be cleaned up (guest files)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_user_files_user_id', 'user_files', ['user_id'])


def downgrade() -> None:
    op.drop_table('user_files')
