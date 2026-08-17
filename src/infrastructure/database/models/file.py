"""File and folder ORM models."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class UserFileModel(Base):
    """Tracks files uploaded by users, stored in S3/Minio."""

    __tablename__ = "user_files"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    folder_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("user_folders.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        comment="Parent folder, or NULL for root-level files",
    )
    file_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="S3 object key")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="Original filename")
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this file should be cleaned up (guest files)",
    )


class UserFolderModel(Base):
    """Folders owned by users, mirroring an online file storage hierarchy."""

    __tablename__ = "user_folders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    parent_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("user_folders.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
        comment="Parent folder, or NULL for a root-level folder",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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

    __table_args__ = (
        UniqueConstraint(
            "user_id", "parent_id", "name", name="uq_user_folders_user_parent_name"
        ),
    )
