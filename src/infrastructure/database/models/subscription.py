"""User subscription ORM model."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Enum as SqlEnum, ForeignKey, Integer, String
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
