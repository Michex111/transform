"""Durable record of processed Stripe webhook events.

Why this table exists
---------------------
Before it, "was this event processed?" could not be answered. The endpoint
returned HTTP 200 from fourteen different paths — including several that wrote
nothing — so a 200 meant only *"the handler returned"*. That made three things
impossible:

* **Idempotency by event id.** The only dedupe that existed was the credit
  ledger's unique ``reference_id``; subscription state had none.
* **Replay.** A missed event could only be recovered by a human opening the
  Stripe dashboard.
* **Audit.** There was no way to say whether a given delivery had any effect.

The row is inserted **before** the handler runs, so a crash mid-processing
leaves a visible ``received`` record rather than silence. ``outcome`` records
what happened: ``processed``, ``duplicate``, or ``skipped:<reason>``.

Retention is bounded: the cleanup worker may prune rows older than its configured
window. The table is an operational log, not a financial record — the credit
ledger remains the financial source of truth.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class StripeWebhookEventModel(Base):
    """One row per Stripe event id seen by the webhook endpoint."""

    __tablename__ = "stripe_webhook_events"

    #: Stripe's ``event.id`` (``evt_...``). The primary key is the idempotency
    #: guard: a duplicate delivery collides on insert rather than being detected
    #: by a racy read-then-write.
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: Stripe event type, e.g. ``invoice.payment_failed``.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: Stripe's ``event.created`` (unix seconds). Used as the ordering guard.
    event_created: Mapped[int | None] = mapped_column(nullable=True)

    #: When our endpoint accepted the delivery.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    #: ``received`` until the handler finishes; then ``processed`` /
    #: ``duplicate`` / ``skipped:<reason>`` / ``failed``.
    outcome: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="received", default="received"
    )

    #: Truncated error text for a failed handler. Never contains secrets.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
