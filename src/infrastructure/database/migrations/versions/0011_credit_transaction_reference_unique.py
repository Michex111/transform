"""Add a unique constraint on credit_transactions.reference_id for idempotency.

Duplicate Stripe webhook deliveries must never double-credit a user. A unique
constraint on ``reference_id`` (the Stripe checkout/session id) turns the
idempotency check in the credit-granting path into a race-safe guard.

Revision ID: 0011_credit_transaction_reference_unique
Revises: 0010_migrate_premium_tiers
Create Date: 2026-08-23
"""

from typing import Sequence

from alembic import op


revision: str = "0011_credit_ref_unique"
down_revision: str | None = "0010_migrate_premium_tiers"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (tests) uses the in-memory schema builder; the ORM model adds
        # the constraint there. Nothing to alter here.
        return

    # Deduplicate any legacy duplicate reference_ids (keep the earliest).
    op.execute(
        """
        DELETE FROM credit_transactions a
        USING credit_transactions b
        WHERE a.reference_id IS NOT NULL
          AND a.reference_id = b.reference_id
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_credit_transactions_reference_id",
        "credit_transactions",
        ["reference_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.drop_constraint(
        "uq_credit_transactions_reference_id",
        "credit_transactions",
        type_="unique",
    )
