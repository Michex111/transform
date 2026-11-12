"""payment grace period and durable stripe webhook event log

Adds the state needed to answer *"is this subscription still paid for?"* without
asking Stripe, plus a durable log of processed webhook events.

Why the lifecycle columns
-------------------------
``user_subscriptions`` previously had exactly one entitlement signal: ``tier``.
There was no column in which to record that a recurring payment had failed, so a
lapsed subscription kept its paid tier indefinitely — ``invoice.payment_failed``
was a log line and ``customer.subscription.updated`` re-applied the paid tier for
``past_due``/``unpaid``. The added columns separate three questions that ``tier``
was being forced to answer at once:

* ``tier``            — the plan the customer bought
* ``stripe_status``   — what Stripe last reported (a mirror)
* ``payment_state``   — whether that plan is currently paid for

Enforced entitlement is **derived** from these by
``domain/subscriptions/policies/payment_grace.effective_tier`` and is never read
straight off ``tier``.

Why no data backfill
--------------------
``payment_state`` defaults to ``'OK'`` and that is correct for every existing
row: the local database holds no evidence of a past failure, and inventing grace
state would be guesswork that could revoke a paying customer's access. Rows whose
Stripe subscription is already ``past_due`` are picked up by the reconciliation
pass, which anchors their window to authoritative billing data instead of to a
guess.

Why the event table
-------------------
The webhook endpoint could not previously answer *"was this event processed?"*,
deduplicate by ``event.id``, or replay anything. ``stripe_webhook_events`` gives
all three, and its primary key on ``event.id`` makes dedupe a constraint rather
than a race.

Safety
------
Fully additive: nullable columns, one non-nullable column with a server default
(metadata-only on PostgreSQL 11+), a new table, and two indexes. No destructive
operation, and nothing here touches customer files, credit balances, or Stripe
identifiers. The upgrade is idempotent — see :func:`upgrade`.

Revision ID: 0029_payment_grace
Revises: 0028_cloud_workspaces
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_payment_grace"
down_revision: Union[str, Sequence[str], None] = "0028_cloud_workspaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUBSCRIPTIONS = "user_subscriptions"
_EVENTS = "stripe_webhook_events"

#: (name, column) pairs, applied and removed together.
_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    ("stripe_status", sa.Column("stripe_status", sa.String(length=32), nullable=True)),
    (
        "payment_state",
        sa.Column(
            "payment_state", sa.String(length=16), nullable=False, server_default="OK"
        ),
    ),
    ("grace_started_at", sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True)),
    ("grace_deadline_at", sa.Column("grace_deadline_at", sa.DateTime(timezone=True), nullable=True)),
    (
        "grace_source_invoice_id",
        sa.Column("grace_source_invoice_id", sa.String(length=255), nullable=True),
    ),
    (
        "last_payment_failed_at",
        sa.Column("last_payment_failed_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "last_payment_event_at",
        sa.Column("last_payment_event_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "previous_paid_tier",
        sa.Column("previous_paid_tier", sa.String(length=20), nullable=True),
    ),
    (
        "entitlement_revoked_at",
        sa.Column("entitlement_revoked_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "current_period_end",
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "cancel_at_period_end",
        sa.Column(
            "cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    ),
)


def _column_names(connection, table: str) -> set[str]:
    """Column names on ``table``, or an empty set when the table is absent."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _index_names(connection, table: str) -> set[str]:
    """Index names on ``table``, tolerating a missing table."""
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def _table_names(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def upgrade() -> None:
    """Add the lifecycle columns, the event log, and the sweep indexes.

    Re-runnable for the same reason 0013/0014/0017/0018/0020/0021 are: the
    alembic version table is the only record of what has been applied, and
    ``RUN_MIGRATIONS=true`` turns a migration error into a *startup* outage. A
    migration applied out of band, or against a database whose stamp was reset,
    must not abort the boot on a duplicate-column error.
    """
    connection = op.get_bind()

    existing_columns = _column_names(connection, _SUBSCRIPTIONS)
    for name, column in _COLUMNS:
        if name not in existing_columns:
            op.add_column(_SUBSCRIPTIONS, column)

    if _EVENTS not in _table_names(connection):
        op.create_table(
            _EVENTS,
            sa.Column("event_id", sa.String(length=255), primary_key=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("event_created", sa.Integer(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "outcome", sa.String(length=64), nullable=False, server_default="received"
            ),
            sa.Column("error", sa.Text(), nullable=True),
        )

    existing_indexes = _index_names(connection, _SUBSCRIPTIONS)
    # The grace sweep selects only rows that are actually in grace, so a
    # partial index keeps it proportional to the number of delinquent
    # subscriptions rather than to the size of the table.
    if "ix_user_subscriptions_grace_deadline" not in existing_indexes:
        op.create_index(
            "ix_user_subscriptions_grace_deadline",
            _SUBSCRIPTIONS,
            ["grace_deadline_at"],
            postgresql_where=sa.text("payment_state = 'GRACE'"),
        )
    # Resolving an invoice event to a local row used to require a Stripe
    # round-trip purely to read ``metadata.user_id``.
    if "ix_user_subscriptions_stripe_subscription_id" not in existing_indexes:
        op.create_index(
            "ix_user_subscriptions_stripe_subscription_id",
            _SUBSCRIPTIONS,
            ["stripe_subscription_id"],
        )

    event_indexes = _index_names(connection, _EVENTS)
    if "ix_stripe_webhook_events_event_type" not in event_indexes:
        op.create_index(
            "ix_stripe_webhook_events_event_type",
            _EVENTS,
            ["event_type"],
        )


def downgrade() -> None:
    """Drop the indexes, the event log, and the lifecycle columns."""
    connection = op.get_bind()

    for table, indexes in (
        (
            _SUBSCRIPTIONS,
            (
                "ix_user_subscriptions_grace_deadline",
                "ix_user_subscriptions_stripe_subscription_id",
            ),
        ),
        (_EVENTS, ("ix_stripe_webhook_events_event_type",)),
    ):
        existing = _index_names(connection, table)
        for index in indexes:
            if index in existing:
                op.drop_index(index, table_name=table)

    if _EVENTS in _table_names(connection):
        op.drop_table(_EVENTS)

    existing_columns = _column_names(connection, _SUBSCRIPTIONS)
    for name, _ in _COLUMNS:
        if name in existing_columns:
            op.drop_column(_SUBSCRIPTIONS, name)
