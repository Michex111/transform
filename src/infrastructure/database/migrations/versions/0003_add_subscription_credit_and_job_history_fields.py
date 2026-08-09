"""Add subscription, credit, and job history fields.

Revision ID: 0003_subscription_credit
Revises: 8e1f4b00d1de
Create Date: 2026-07-25 19:10:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_subscription_credit"
down_revision: str | None = "8e1f4b00d1de"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _subscription_tier_enum(create_type: bool = True) -> sa.Enum:
    return postgresql.ENUM(
        "GUEST",
        "FREE",
        "PREMIUM",
        name="subscriptiontier",
        create_type=create_type,
    )


def upgrade() -> None:
    op.add_column("conversion_jobs", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("conversion_jobs", sa.Column("error_message", sa.String(), nullable=True))
    op.add_column(
        "conversion_jobs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "conversion_jobs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_conversion_jobs_user_id", "conversion_jobs", ["user_id"], unique=False)
    op.execute("UPDATE conversion_jobs SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    op.execute("UPDATE conversion_jobs SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
    op.alter_column("conversion_jobs", "created_at", nullable=False)
    op.alter_column("conversion_jobs", "updated_at", nullable=False)

    tier_enum = _subscription_tier_enum(create_type=False)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _subscription_tier_enum(create_type=True).create(bind, checkfirst=True)

    op.create_table(
        "user_subscriptions",
        sa.Column("actor_key", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("tier", tier_enum, nullable=False),
        sa.Column("used_storage_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("actor_key"),
    )
    op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"], unique=True)

    op.create_table(
        "monthly_credits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("period_key", sa.String(length=7), nullable=False),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "period_key", name="uq_monthly_credits_owner_period"),
    )
    op.create_index("ix_monthly_credits_owner_id", "monthly_credits", ["owner_id"], unique=False)
    op.create_index("ix_monthly_credits_period_key", "monthly_credits", ["period_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_monthly_credits_period_key", table_name="monthly_credits")
    op.drop_index("ix_monthly_credits_owner_id", table_name="monthly_credits")
    op.drop_table("monthly_credits")

    op.drop_index("ix_user_subscriptions_user_id", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")

    tier_enum = _subscription_tier_enum(create_type=True)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        tier_enum.drop(bind, checkfirst=True)

    op.drop_index("ix_conversion_jobs_user_id", table_name="conversion_jobs")
    op.drop_column("conversion_jobs", "updated_at")
    op.drop_column("conversion_jobs", "created_at")
    op.drop_column("conversion_jobs", "error_message")
    op.drop_column("conversion_jobs", "user_id")
