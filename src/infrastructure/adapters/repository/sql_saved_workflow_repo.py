"""SQL adapter for saved workflows.

Every query carries ``user_id`` in its ``WHERE`` clause. That is the whole
authorization model for this table, and it is enforced here rather than in the
service so that no caller can widen it by forgetting a check.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.workflows.entities.saved_workflow import (
    InvalidWorkflowError,
    SavedWorkflow,
    parse_definition,
)
from src.infrastructure.database.models import SavedWorkflowModel


class SQLSavedWorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_workflow(self, workflow: SavedWorkflow) -> None:
        self.session.add(
            SavedWorkflowModel(
                workflow_id=workflow.workflow_id,
                user_id=workflow.user_id,
                name=workflow.name,
                description=workflow.description,
                definition=workflow.definition.to_dict(),
            )
        )
        await self.session.commit()

    async def update_workflow(self, workflow: SavedWorkflow) -> bool:
        stmt = (
            update(SavedWorkflowModel)
            .where(
                SavedWorkflowModel.workflow_id == workflow.workflow_id,
                # Ownership is in the UPDATE's own predicate, not a separate
                # read-then-write: a check performed before the write is a race
                # and, more practically, a second place to forget.
                SavedWorkflowModel.user_id == workflow.user_id,
            )
            .values(
                name=workflow.name,
                description=workflow.description,
                definition=workflow.definition.to_dict(),
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def delete_workflow(self, workflow_id: str, user_id: int) -> bool:
        stmt = delete(SavedWorkflowModel).where(
            SavedWorkflowModel.workflow_id == workflow_id,
            SavedWorkflowModel.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def get_workflow(self, workflow_id: str, user_id: int) -> SavedWorkflow | None:
        stmt = select(SavedWorkflowModel).where(
            SavedWorkflowModel.workflow_id == workflow_id,
            SavedWorkflowModel.user_id == user_id,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return self._to_entity(row) if row is not None else None

    async def list_workflows(self, user_id: int) -> list[SavedWorkflow]:
        stmt = (
            select(SavedWorkflowModel)
            .where(SavedWorkflowModel.user_id == user_id)
            .order_by(SavedWorkflowModel.updated_at.desc(), SavedWorkflowModel.created_at.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._to_entity(row) for row in rows]

    @staticmethod
    def _to_entity(row: SavedWorkflowModel) -> SavedWorkflow:
        """Map a row to the domain entity.

        The stored JSON is re-validated on the way out rather than trusted. It
        was validated on the way in, but a definition edited directly in the
        database, or written by an older revision of this code, would otherwise
        reach the runner unchecked — and the runner is where a malformed
        definition would do damage. A row that no longer parses is surfaced as
        an :class:`InvalidWorkflowError`, which the API turns into a clear
        failure rather than a run that silently does the wrong thing.
        """
        try:
            definition = parse_definition(row.definition)
        except InvalidWorkflowError as exc:
            raise InvalidWorkflowError(
                f"Stored workflow {row.workflow_id} has an invalid definition: {exc}"
            ) from exc

        return SavedWorkflow(
            workflow_id=row.workflow_id,
            user_id=row.user_id,
            name=row.name,
            description=row.description or "",
            definition=definition,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
