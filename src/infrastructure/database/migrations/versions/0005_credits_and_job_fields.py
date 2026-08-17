"""add compute fields to conversion_jobs and credit_transactions table

Revision ID: 0005_credits_and_job_fields
Revises: 0004_user_files
Create Date: 2026-08-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_credits_and_job_fields'
down_revision: Union[str, Sequence[str], None] = '0004_user_files'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversion_jobs',
        sa.Column('compute_duration_ms', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'conversion_jobs',
        sa.Column('credits_used', sa.Integer(), nullable=False, server_default='0'),
    )

    op.add_column(
        'user_subscriptions',
        sa.Column('stripe_customer_id', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'user_subscriptions',
        sa.Column('stripe_subscription_id', sa.String(length=255), nullable=True),
    )

    op.create_table(
        'credit_transactions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(length=20), nullable=False),
        sa.Column('reference_id', sa.String(length=255), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_credit_transactions_user_id', 'credit_transactions', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_credit_transactions_user_id', table_name='credit_transactions')
    op.drop_table('credit_transactions')
    op.drop_column('user_subscriptions', 'stripe_subscription_id')
    op.drop_column('user_subscriptions', 'stripe_customer_id')
    op.drop_column('conversion_jobs', 'credits_used')
    op.drop_column('conversion_jobs', 'compute_duration_ms')
