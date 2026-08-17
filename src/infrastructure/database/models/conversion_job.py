"""Conversion job ORM model."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum as SqlEnum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.conversions.value_object.job_status import JobStatus
from src.infrastructure.database.session import Base


class ConversionJobModel(Base):
    __tablename__ = "conversion_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[JobStatus] = mapped_column(SqlEnum(JobStatus, name="jobstatus"), nullable=False)
    source_format: Mapped[str] = mapped_column(String, nullable=False)
    target_format: Mapped[str] = mapped_column(String, nullable=False)
    input_file: Mapped[str] = mapped_column(String, nullable=False)
    output_file: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    compute_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
