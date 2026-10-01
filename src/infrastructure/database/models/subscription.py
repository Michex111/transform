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
