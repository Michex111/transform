"""User subscription ORM model."""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.session import Base


class UserSubscriptionModel(Base):
    """Stores the active subscription tier and storage usage per actor."""

    __tablename__ = "user_subscriptions"

    actor_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    tier: Mapped[SubscriptionTier] = mapped_column(
        SqlEnum(SubscriptionTier, name="subscriptiontier"), nullable=False
    )
    used_storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Credit wallet -----------------------------------------------------
    # The three credit populations with different lifetimes. See migration
    # `0020_credit_wallet` for why they live here and why nothing is backfilled.
    #
    # `monthly_credits` remains the PLAN bucket (one row per period); these
    # columns hold what that table cannot express — credits that outlive their
    # period (purchases) and credits that expire on a date unrelated to the
    # calendar month (an upgrade's carryover).
    #
    # `server_default` mirrors the migration so a row inserted by either path
    # gets the same values; `default` keeps ORM-constructed rows consistent
    # before the insert.
    purchased_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    carryover_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    carryover_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Spend purchased credits before the plan's own. Off by default so the
    #: plan grant — which resets anyway — is consumed first, and credits the
    #: user paid for are held in reserve.
    purchased_credits_first: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )

    # --- Payment lifecycle -------------------------------------------------
    # Three separate axes, deliberately not collapsed into ``tier`` (see
    # ``domain/subscriptions/value_object/payment_state.py``):
    #
    #   * ``tier``            — the plan the customer bought.
    #   * ``stripe_status``   — the last status Stripe reported (a mirror only).
    #   * ``payment_state``   — whether that plan is currently paid for.
    #
    # Entitlement is DERIVED from all three by
    # ``payment_grace.effective_tier``; it is never read straight off ``tier``.
    # Without this split a lapsed subscription had nowhere to record that it had
    # lapsed, so every gate kept granting the purchased plan indefinitely.

    #: Raw Stripe subscription status, mirrored for reconciliation and so a
    #: terminal status cannot be masked by a stale ``tier``. Nullable because
    #: rows created before this column existed have no Stripe status.
    stripe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: ``OK`` / ``GRACE`` / ``REVOKED``. A plain string, not a PG enum, so a
    #: future state needs no ``ALTER TYPE`` (see migration 0021's rationale).
    payment_state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="OK", default="OK"
    )

    #: Instant the current grace window started (the failing invoice's
    #: creation time). Kept for audit; the deadline is authoritative.
    grace_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Absolute end of the grace window. Written **once** per window: duplicate
    #: or replayed failures must never move it.
    grace_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Stripe invoice that opened the window (audit / replay identification).
    grace_source_invoice_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    #: Last time a payment failure was observed, regardless of state.
    last_payment_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Ordering guard: the Stripe ``event.created`` of the newest event applied
    #: to this row. An older event arriving late is dropped instead of
    #: overwriting newer state.
    last_payment_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Plan to restore if payment recovers after revocation. A string rather
    #: than the enum so a recovery path never has to cast to the PG type.
    previous_paid_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)

    #: When the local entitlement was actually removed.
    entitlement_revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Last known end of the paid period, read from the subscription ITEM
    #: (Stripe moved this field off the subscription object). Persisted so
    #: "has the paid period ended?" no longer requires a Stripe round-trip.
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Whether the subscription is scheduled to lapse at period end.
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
