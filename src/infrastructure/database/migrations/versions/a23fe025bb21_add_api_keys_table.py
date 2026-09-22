"""add api_keys table

Revision ID: a23fe025bb21
Revises: 0003_subscription_credit
Create Date: 2026-08-06 21:25:19.380274

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a23fe025bb21'
down_revision: Union[str, Sequence[str], None] = '0003_subscription_credit'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APIKEYSTATUS_VALUES = ('ACTIVE', 'INACTIVE', 'REVOKED')


def _apikeystatus_enum(create_type: bool = True) -> postgresql.ENUM:
    """The ``apikeystatus`` enum, created explicitly rather than implicitly.

    See ``0001_create_conversion_jobs``: an ``sa.Enum`` inlined into
    ``create_table`` issues an unguarded ``CREATE TYPE`` and aborts
    ``upgrade head`` on any database where the type already exists (types
    survive a ``DROP TABLE`` in PostgreSQL).
    """
    return postgresql.ENUM(*_APIKEYSTATUS_VALUES, name='apikeystatus', create_type=create_type)


def upgrade() -> None:
    """Upgrade schema."""
    apikeystatus = _apikeystatus_enum(create_type=False)
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        _apikeystatus_enum(create_type=True).create(bind, checkfirst=True)

    op.create_table(
        'api_keys',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('status', apikeystatus, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rate_limit_per_usage', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_api_keys_key'), 'api_keys', ['key'], unique=True)
    op.create_index(op.f('ix_api_keys_user_id'), 'api_keys', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_api_keys_user_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_key'), table_name='api_keys')
    op.drop_table('api_keys')
    op.execute('DROP TYPE IF EXISTS apikeystatus')
