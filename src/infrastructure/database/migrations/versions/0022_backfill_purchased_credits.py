"""backfill purchased credits out of the legacy merged plan bucket

This migration **carries data statements** — unlike ``0020_credit_wallet`` and
``0021_job_origin``, which are pure additive schema changes. Read the rule below
before changing anything here.

Before ``0020`` there was one credit number. ``_grant_purchased_credits`` merged
a purchase INTO the plan bucket (``allowance += credits; remaining += credits``),
so after buying a pack a user's ``monthly_credits.allowance`` was the tier grant
PLUS everything they had ever bought. ``0020`` added a separate
``user_subscriptions.purchased_credits`` column but deliberately did not move
anything, because it "would be guessing at which portion of an existing balance
was paid for". It is not a guess any more: the ``credit_transactions`` ledger
still records every ``PURCHASE``, and bought credits never expire, so the ledger
is an exact upper bound on how much of the merged bucket was paid for.

Left alone, the merged portion is **destroyed at the next calendar-month reset**:
a new period starts a fresh bucket at the tier grant, so the purchased credits
that were sitting inside the old bucket simply vanish. This migration is
therefore credit-preserving, not cosmetic.

The rule, for each user's CURRENT period bucket::

    tier_grant         = TierPolicy.for_tier(tier).monthly_conversion_credits
    purchased_portion  = max(0, allowance - tier_grant)     # how old code built it
    ledger_purchases   = SUM(credit_transactions.amount WHERE type='PURCHASE')
    purchased_remaining = min(remaining, purchased_portion, ledger_purchases)
    monthly_credits.allowance  -= min(purchased_portion, allowance)
    monthly_credits.remaining  -= purchased_remaining
    user_subscriptions.purchased_credits += purchased_remaining

Conservation is the invariant, and it is **exact**: this is a re-labelling,
never a grant and never a clawback. For a row whose purchased column was still
zero, ``before = remaining`` equals ``after = plan_remaining + purchased``. A
row written after the ``0020`` split may already have purchased credits in their
own column; those cancel on both sides, so the check is written as
``remaining + existing_purchased == new_remaining + new_purchased``. A row that
would fail the check (or would break ``remaining <= allowance``) is skipped and
logged rather than written.

Two guards matter more than the arithmetic:

* **The ledger caps the relabel.** ``ledger_purchases == 0`` means the user never
  bought a pack, so nothing may be moved even if their allowance looks inflated.
  Without this, plan credits would be mislabelled as (permanent, non-expiring)
  purchased credits — a silent upgrade of the user's balance.
* **Reducing the allowance by the full portion is what makes this idempotent.**
  Afterwards ``allowance == tier_grant``, so ``purchased_portion`` is 0 and a
  re-run moves nothing. ``RUN_MIGRATIONS=true`` makes a migration error a
  *startup* failure, so every statement here must tolerate being applied twice.

Rows with no subscription, no bucket for the current period, or a tier with no
finite grant (``GUEST`` / ``ENTERPRISE``) are skipped: their balance cannot be
decomposed as "grant + purchases".

``downgrade`` folds the current period's purchased credits back into the plan
bucket (``allowance += purchased; remaining += purchased; purchased = 0``),
restoring the pre-split merged semantics. It is also idempotent by
construction, and ``remaining + purchased <= allowance + purchased`` holds, so
it cannot violate the bucket invariant.

Revision ID: 0022_backfill_purchased_credits
Revises: 0021_job_origin
Create Date: 2026-10-01
"""

import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.domain.subscriptions.policies.tier_policy import TierPolicy
from src.domain.subscriptions.value_object.credit_period import current_period_key
from src.domain.subscriptions.value_object.tier import SubscriptionTier

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = "0022_backfill_purchased_credits"
# 0021_job_origin is the current head on this branch. Chaining off it keeps a
# single linear head; revising 0020 directly would fork alembic into two heads
# and make ``upgrade head`` ambiguous.
down_revision: Union[str, Sequence[str], None] = "0021_job_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUBSCRIPTIONS = "user_subscriptions"
_CREDITS = "monthly_credits"
_TRANSACTIONS = "credit_transactions"
_PURCHASE = "PURCHASE"

_REQUIRED_CREDIT_COLUMNS = {"owner_id", "period_key", "allowance", "remaining"}
_REQUIRED_SUBSCRIPTION_COLUMNS = {"user_id", "tier", "purchased_credits"}


def _column_names(connection, table: str) -> set[str]:
    """Column names on ``table``, or an empty set when the table is absent."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _has_required_columns(connection) -> bool:
    """Whether the schema is new enough for this data migration to run.

    ``user_subscriptions.purchased_credits`` only exists from ``0020`` onwards;
    a database alembic thinks is at this revision but where the column is
    missing must not abort the boot, so the whole migration degrades to a no-op.
    The ledger table is included because it is the cap that makes the relabel
    safe — without it, nothing may be moved.
    """
    if not _REQUIRED_CREDIT_COLUMNS <= _column_names(connection, _CREDITS):
        return False
    if not _REQUIRED_SUBSCRIPTION_COLUMNS <= _column_names(connection, _SUBSCRIPTIONS):
        return False
    return bool(_column_names(connection, _TRANSACTIONS))


def _coerce_user_id(owner_id: object) -> int | None:
    """The numeric user id behind a ``monthly_credits.owner_id``, or None.

    Owner ids are stored as strings; a non-numeric one (there is none today, but
    the column is a plain ``String``) simply has no subscription to match.
    """
    try:
        return int(str(owner_id))
    except (TypeError, ValueError):
        return None


def _grant_for_tier(tier_value: object) -> int | None:
    """A tier's finite monthly plan grant, or None when it has none.

    ``GUEST`` and ``ENTERPRISE`` are unbounded, so "grant + purchases" cannot be
    decomposed for them. An unrecognised tier degrades to None for the same
    reason: skip rather than guess.
    """
    try:
        tier = SubscriptionTier(str(tier_value))
    except ValueError:
        return None
    return TierPolicy.for_tier(tier).monthly_conversion_credits


def _ledger_purchase_total(connection, user_id: int) -> int:
    """All-time PURCHASE amount for a user.

    All-time (not per-period) because bought credits never expire, so a pack
    bought several periods ago still funds the current balance.
    """
    total = connection.execute(
        sa.text(
            "SELECT COALESCE(SUM(amount), 0) FROM credit_transactions "
            "WHERE user_id = :user_id AND transaction_type = :transaction_type"
        ),
        {"user_id": user_id, "transaction_type": _PURCHASE},
    ).scalar()
    return int(total or 0)


def _subscription_row(connection, user_id: int):
    row = connection.execute(
        sa.text(
            "SELECT tier, purchased_credits FROM user_subscriptions "
            "WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    ).fetchone()
    return row


def upgrade() -> None:
    """Re-label the legacy merged purchased portion as purchased credits."""
    connection = op.get_bind()
    if not _has_required_columns(connection):
        logger.info(
            "0022: schema too old for the purchased-credit backfill; skipping"
        )
        return

    period_key = current_period_key()
    rows = connection.execute(
        sa.text(
            "SELECT owner_id, allowance, remaining FROM monthly_credits "
            "WHERE period_key = :period_key"
        ),
        {"period_key": period_key},
    ).fetchall()

    for owner_id, raw_allowance, raw_remaining in rows:
        user_id = _coerce_user_id(owner_id)
        if user_id is None:
            continue
        subscription = _subscription_row(connection, user_id)
        if subscription is None:
            # No subscription row ⇒ no tier, so no grant to decompose against.
            continue
        tier_value, raw_purchased = subscription
        tier_grant = _grant_for_tier(tier_value)
        if tier_grant is None:
            continue

        allowance = int(raw_allowance or 0)
        remaining = int(raw_remaining or 0)
        existing_purchased = int(raw_purchased or 0)
        purchased_portion = max(0, allowance - tier_grant)
        if purchased_portion == 0:
            # Already at (or below) the tier grant: nothing merged, nothing to
            # relabel. This is also the steady state a re-run sees.
            continue

        ledger_purchases = _ledger_purchase_total(connection, user_id)
        if ledger_purchases <= 0:
            # THE guard. This user never bought a pack, so nothing may be moved;
            # mislabelling plan credits as permanent purchased credits would be
            # a silent grant.
            continue

        purchased_remaining = min(remaining, purchased_portion, ledger_purchases)
        # Reduce by the full portion (capped at the allowance) so the bucket
        # ends at the tier grant — that is what makes a second run a no-op.
        new_allowance = allowance - min(purchased_portion, allowance)
        new_remaining = remaining - purchased_remaining
        new_purchased = existing_purchased + purchased_remaining

        # Conservation, generalised for a row that already had purchased credits
        # in their own column (written after the 0020 split): those cancel on
        # both sides, so this reduces to `remaining == plan + purchased` for the
        # legacy rows this migration actually targets.
        before_total = remaining + existing_purchased
        after_total = new_remaining + new_purchased
        if after_total != before_total:
            logger.warning(
                "0022: skipping user=%s: conservation would break (%s -> %s)",
                user_id,
                before_total,
                after_total,
            )
            continue
        if new_remaining > new_allowance:
            logger.warning(
                "0022: skipping user=%s: bucket invariant would break "
                "(remaining=%s > allowance=%s)",
                user_id,
                new_remaining,
                new_allowance,
            )
            continue

        connection.execute(
            sa.text(
                "UPDATE monthly_credits SET allowance = :allowance, "
                "remaining = :remaining WHERE owner_id = :owner_id "
                "AND period_key = :period_key"
            ),
            {
                "allowance": new_allowance,
                "remaining": new_remaining,
                "owner_id": owner_id,
                "period_key": period_key,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE user_subscriptions SET purchased_credits = :purchased "
                "WHERE user_id = :user_id"
            ),
            {"purchased": new_purchased, "user_id": user_id},
        )
        logger.info(
            "0022: user=%s relabelled %s credit(s) as purchased "
            "(plan %s -> %s)",
            user_id,
            purchased_remaining,
            remaining,
            new_remaining,
        )


def downgrade() -> None:
    """Fold the current period's purchased credits back into the plan bucket.

    Restores the pre-split semantics: with one merged bucket, the plan allowance
    is again the tier grant plus everything bought. Idempotent by construction —
    after one run the purchased column is 0, so a second run moves nothing.
    """
    connection = op.get_bind()
    if not _has_required_columns(connection):
        logger.info("0022: downgrade skipped; schema too old")
        return

    period_key = current_period_key()
    rows = connection.execute(
        sa.text(
            "SELECT owner_id, allowance, remaining FROM monthly_credits "
            "WHERE period_key = :period_key"
        ),
        {"period_key": period_key},
    ).fetchall()

    for owner_id, raw_allowance, raw_remaining in rows:
        user_id = _coerce_user_id(owner_id)
        if user_id is None:
            continue
        subscription = _subscription_row(connection, user_id)
        if subscription is None:
            continue
        purchased = int(subscription[1] or 0)
        if purchased == 0:
            continue

        connection.execute(
            sa.text(
                "UPDATE monthly_credits SET allowance = :allowance, "
                "remaining = :remaining WHERE owner_id = :owner_id "
                "AND period_key = :period_key"
            ),
            {
                "allowance": int(raw_allowance or 0) + purchased,
                "remaining": int(raw_remaining or 0) + purchased,
                "owner_id": owner_id,
                "period_key": period_key,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE user_subscriptions SET purchased_credits = 0 "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
