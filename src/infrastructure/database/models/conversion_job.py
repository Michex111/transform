"""Conversion job ORM model."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SqlEnum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.conversions.value_object.job_origin import JobOrigin
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
    # How the request that created the job authenticated (web session, API key,
    # or guest). Stored as a plain ``String(16)`` rather than a Postgres native
    # ENUM on purpose: adding a value to a native enum needs its own committed
    # migration, and this repo has already been burned by that split (0009/0010).
    # ``server_default`` because the column is non-nullable and the production
    # table is populated — see migration 0021.
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobOrigin.WEB.value, server_default="WEB"
    )
    # Percentage the worker has reached (0/25/50/75/100). Persisted so a client
    # that reloads a chat or reconnects can show the real bar without waiting for
    # the SSE stream to replay. 0 = "not reported yet", rendered as indeterminate.
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Groups the jobs created by one batch request. Nullable, because the
    # overwhelming majority of jobs are single conversions and a NULL is the
    # honest representation of "not part of a batch".
    #
    # There is deliberately NO parent `batches` table: the jobs are already
    # persisted, already carry their own status, and are already streamed
    # individually, so a parent row would be a second copy of state that can
    # drift out of step with its children. A batch is a *query* over this
    # column (see `list_by_batch`).
    #
    # Indexed because listing a batch is exactly the access pattern it serves.
    batch_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    # The saved workflow this job was produced by, when it came from a run.
    #
    # Not a foreign key on purpose. A hard FK would make deleting a workflow
    # either fail or cascade into the user's conversion history — and that
    # history is the user's own record of work they actually did, which must
    # outlive the shortcut that produced it. A dangling id here is harmless:
    # nothing joins on it, and the runs view simply stops naming a workflow that
    # no longer exists.
    workflow_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
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
