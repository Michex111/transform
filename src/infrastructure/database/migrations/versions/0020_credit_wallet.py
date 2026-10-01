"""add credit wallet columns to user_subscriptions

Splits the single merged credit bucket into the three populations the billing
model actually needs, each with a different lifetime:

* ``purchased_credits``     — bought credit packs. **Never expire.**
* ``carryover_credits``     — the unspent remainder of a plan the user upgraded
  away from. Expires (see ``carryover_expires_at``) so it bridges the gap to the
  old renewal date rather than becoming permanent.
* ``carryover_expires_at``  — when the carryover is zeroed. This is the OLD
  subscription's ``current_period_end`` captured at upgrade time, which is why
  it cannot be derived from the calendar-month ``period_key`` used by
  ``monthly_credits``.

``monthly_credits`` is deliberately left in place and unchanged: it remains the
plan's own per-period bucket, and it is what a worker that has not yet been
rebuilt still reads and decrements. Nothing here changes anyone's balance — the
columns are additive, defaulted, and unread until the consumption path opts in.

Why these live on ``user_subscriptions`` rather than a new table: that table is
already one-row-per-user (``user_id`` is UNIQUE) and already owns the
billing relationship, so a second table would add a join and a second place for
the same row to go missing, with no benefit.

No backfill is required and none is attempted. Every existing account starts at
``purchased_credits = 0``/``carryover_credits = 0``, which is *correct for the
current data*: before this change, purchases were merged into
``monthly_credits.allowance`` and the ledger in ``credit_transactions`` still
records every purchase, so the split can be computed later if wanted. An
invented backfill would be guessing at which portion of an existing balance was
paid for.

The upgrade is **idempotent** — see :func:`upgrade`.

Revision ID: 0020_credit_wallet
Revises: 0019_ai_assistant
Create Date: 2026-10-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0020_credit_wallet"
down_revision: Union[str, Sequence[str], None] = "0019_ai_assistant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "user_subscriptions"

#: (name, column) pairs, applied and removed together.
_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    (
        "purchased_credits",
        sa.Column("purchased_credits", sa.Integer(), nullable=False, server_default="0"),
    ),
    (
        "carryover_credits",
        sa.Column("carryover_credits", sa.Integer(), nullable=False, server_default="0"),
    ),
    (
        "carryover_expires_at",
        sa.Column("carryover_expires_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "purchased_credits_first",
        sa.Column(
            "purchased_credits_first",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    ),
)


def _column_names(connection, table: str) -> set[str]:
    """Column names on ``table``, or an empty set when the table is absent."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """Add the wallet columns, tolerating an already-applied run.

    Re-runnable for the same reason ``0013``/``0014``/``0017``/``0018`` are: the
    alembic version table is the only record of what has been applied, and
    ``RUN_MIGRATIONS=true`` turns a migration error into a *startup* failure (a
    total outage). A migration applied out of band — or against a database whose
    stamp was reset — must not abort the boot on a duplicate-column error.

    ``server_default`` is set on every non-nullable column so the ADD COLUMN
    succeeds against a populated table without a separate backfill statement.
    """
    connection = op.get_bind()
    existing = _column_names(connection, _TABLE)

    for name, column in _COLUMNS:
        if name in existing:
            continue
        op.add_column(_TABLE, column)


def downgrade() -> None:
    """Drop the wallet columns."""
    connection = op.get_bind()
    existing = _column_names(connection, _TABLE)

    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column(_TABLE, name)
