"""Conversion job ORM model."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, Integer, String
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
    object_key: Mapped[str] = mapped_column(String, nullable=False, default="")
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    compute_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Plaintext bytes moved, measured by the worker. 0 means "not measured"
    # (a job that has not run, or one recorded before these columns existed),
    # which the API and the SPA render as absent rather than as an empty file.
    # BigInteger because a single object may be up to the PRO_PLUS 1 GB limit.
    input_size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    output_size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    # Client-side (FENCR) encryption metadata. ``data_key_wrapped`` holds the
    # Fernet ciphertext (base64 str) of the client's raw per-file data key,
    # wrapped with the per-user derived key at job creation. The raw key is
    # never stored. ``client_encrypted`` flags a FENCR blob input.
    data_key_wrapped: Mapped[str | None] = mapped_column(String, nullable=True)
    client_encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
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
