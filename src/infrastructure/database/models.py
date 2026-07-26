from src.infrastructure.database.session import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, Integer, String, UniqueConstraint
from datetime import UTC, datetime

from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier

class ConversionJobModel(Base):
    __tablename__ = "conversion_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(SqlEnum(JobStatus), nullable=False)
    source_format: Mapped[str] = mapped_column(String, nullable=False)
    target_format: Mapped[str] = mapped_column(String, nullable=False)
    input_file: Mapped[str] = mapped_column(String, nullable=False)
    output_file: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
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


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class UserSubscriptionModel(Base):
    """Persistent subscription state for one tracked actor."""

    __tablename__ = "user_subscriptions"

    actor_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    tier: Mapped[SubscriptionTier] = mapped_column(
        SqlEnum(SubscriptionTier, name="subscriptiontier"),
        nullable=False,
    )
    used_storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
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


class MonthlyCreditModel(Base):
    """Monthly conversion credit ledger for authenticated users."""

    __tablename__ = "monthly_credits"
    __table_args__ = (
        UniqueConstraint("owner_id", "period_key", name="uq_monthly_credits_owner_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    period_key: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    allowance: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
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
