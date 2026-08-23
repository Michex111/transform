"""Migrate legacy PREMIUM subscription rows to the concrete PRO tier.

This must run AFTER 0009_add_paid_tiers commits the new enum values (PRO,
PRO_PLUS, ENTERPRISE); PostgreSQL cannot use a newly-added enum value within the
same transaction that added it.

Revision ID: 0010_migrate_premium_tiers
Revises: 0009_add_paid_tiers
Create Date: 2026-08-22
"""

from typing import Sequence

from alembic import op


revision: str = "0010_migrate_premium_tiers"
down_revision: str | None = "0009_add_paid_tiers"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (tests) has no real enum; nothing to migrate.
        return

    # Collapse any legacy PREMIUM rows to the concrete PRO tier.
    op.execute("UPDATE user_subscriptions SET tier = 'PRO' WHERE tier = 'PREMIUM'")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # PostgreSQL cannot remove enum values. Collapse the new tiers back to the
    # legacy PREMIUM value so the downgrade is consistent with the previous
    # single-paid-tier behavior.
    op.execute(
        "UPDATE user_subscriptions SET tier = 'PREMIUM' WHERE tier IN ('PRO', 'PRO_PLUS', 'ENTERPRISE')"
    )
