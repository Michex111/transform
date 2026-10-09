"""Saved-workflow ORM model.

One row per stored workflow. The definition is a JSON column rather than a set
of child rows: it is always read and written whole, nothing queries *inside* it,
and its shape is owned by the domain (`parse_definition`), which is the only
thing allowed to interpret it.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.session import Base


class SavedWorkflowModel(Base):
    __tablename__ = "saved_workflows"

    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(
        String(400), nullable=False, default="", server_default=""
    )
    # The validated definition, as `WorkflowDefinition.to_dict()` produced it.
    # `nullable=False` because a workflow without a definition is not a workflow;
    # a row could not have been created without one (see `WorkflowService`).
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
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
