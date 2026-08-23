"""Add distinct paid subscription tiers to the subscriptiontier enum.

Revision ID: 0009_add_paid_tiers
Revises: 0008_add_favorite_flag
Create Date: 2026-08-22
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_add_paid_tiers"
down_revision: str | None = "0008_add_favorite_flag"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _subscription_tier_enum(create_type: bool = True) -> postgresql.ENUM:
    """The full subscriptiontier enum, including the new paid tiers."""
    return postgresql.ENUM(
        "GUEST",
        "FREE",
        "PREMIUM",
        "PRO",
        "PRO_PLUS",
        "ENTERPRISE",
        name="subscriptiontier",
        create_type=create_type,
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (tests) has no real enum; the model already accepts the new
        # values so no schema change is needed.
        return

    # PostgreSQL cannot add enum values then use them in the SAME transaction:
    # "unsafe use of new value ... New enum values must be committed before they
    # can be used." The ALTER TYPE ... ADD VALUE statements below commit when this
    # migration's transaction commits. The data migration (PREMIUM -> PRO) is
    # intentionally in a SEPARATE migration (0010) so it runs only after these
    # new values are committed and usable.
    # Raw string literals (not bind params) are used because ALTER TYPE ... ADD
    # VALUE does not support bound parameters.
    for value in ("PRO", "PRO_PLUS", "ENTERPRISE"):
        op.execute(
            "ALTER TYPE subscriptiontier ADD VALUE IF NOT EXISTS '{}'".format(value)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # PostgreSQL cannot remove enum values. Any rows referencing the new tiers
    # are collapsed back to PRO by the 0010 downgrade; the enum values
    # themselves cannot be dropped.
    pass
