"""SQL adapters for workspaces, their runtime and their environment snapshots.

Three adapters in one module because they are one aggregate: a workspace, the
runtime attached to it, and the snapshots of its environment are always read and
written together, and splitting them across files would only spread the same
ownership predicate over more places to forget it.

Every user-facing read carries ``user_id`` in its ``WHERE`` clause rather than
trusting a caller to have checked ownership earlier — the same rule
``sql_saved_workflow_repo`` follows, and for the same reason.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.entities.workspace import Workspace
from src.domain.workspaces.entities.workspace_runtime import WorkspaceRuntime
from src.domain.workspaces.value_object.image_revision_status import ImageRevisionStatus
from src.domain.workspaces.value_object.runtime_config import RuntimeConfig
from src.domain.workspaces.value_object.runtime_state import RuntimeState
from src.domain.workspaces.value_object.workspace_state import WorkspaceState
from src.infrastructure.database.models.workspace import (
    RuntimeImageRevisionModel,
    WorkspaceModel,
    WorkspaceRuntimeModel,
)


def aware(value: datetime | None) -> datetime | None:
    """Normalise a stored timestamp to an aware one.

    SQLite returns naive datetimes for ``DateTime(timezone=True)``, and the
    domain compares timestamps. This is the same fix ``sql_mcp_repo`` applies to
    its timestamps, kept local for the same reason: it is a storage quirk, so it
    belongs in the storage adapter.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _workspace_to_entity(row: WorkspaceModel) -> Workspace:
    return Workspace(
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        name=row.name,
        description=row.description or "",
        runtime_config=RuntimeConfig.from_dict(row.runtime_config),
        state=WorkspaceState(row.state),
        created_at=aware(row.created_at),
        updated_at=aware(row.updated_at),
        last_activity_at=aware(row.last_activity_at),
        execution_count=row.execution_count or 0,
    )


def _runtime_to_entity(row: WorkspaceRuntimeModel) -> WorkspaceRuntime:
    return WorkspaceRuntime(
        runtime_id=row.runtime_id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        state=RuntimeState(row.state),
        provider_ref=row.provider_ref,
        image_revision_id=row.image_revision_id,
        resource_profile=row.resource_profile,
        failure_reason=row.failure_reason,
        created_at=aware(row.created_at),
        started_at=aware(row.started_at),
        stopped_at=aware(row.stopped_at),
        last_activity_at=aware(row.last_activity_at),
        provider_metadata=dict(row.provider_metadata or {}),
    )


def _revision_to_entity(row: RuntimeImageRevisionModel) -> RuntimeImageRevision:
    return RuntimeImageRevision(
        revision_id=row.revision_id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        config_fingerprint=row.config_fingerprint,
        base_image_revision=row.base_image_revision,
        status=ImageRevisionStatus(row.status),
        provider_snapshot_ref=row.provider_snapshot_ref,
        created_at=aware(row.created_at),
        ready_at=aware(row.ready_at),
        error=row.error,
        validation_metadata=dict(row.validation_metadata or {}),
    )


class SQLWorkspaceRepository:
    """Workspace persistence, scoped by owner on every read and write."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_workspace(self, workspace: Workspace) -> None:
        self.session.add(
            WorkspaceModel(
                workspace_id=workspace.workspace_id,
                user_id=workspace.user_id,
                name=workspace.name,
                description=workspace.description,
                runtime_config=workspace.runtime_config.to_dict(),
                state=str(workspace.state),
                execution_count=workspace.execution_count,
            )
        )
        await self.session.commit()

    async def update_workspace(self, workspace: Workspace) -> bool:
        statement = (
            update(WorkspaceModel)
            .where(
                WorkspaceModel.workspace_id == workspace.workspace_id,
                # Ownership in the UPDATE itself: a mismatched owner affects no
                # rows and reports False rather than writing to someone else's
                # workspace.
                WorkspaceModel.user_id == workspace.user_id,
            )
            .values(
                name=workspace.name,
                description=workspace.description,
                runtime_config=workspace.runtime_config.to_dict(),
                state=str(workspace.state),
                execution_count=workspace.execution_count,
                last_activity_at=workspace.last_activity_at,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def delete_workspace(self, workspace_id: str, user_id: int) -> bool:
        statement = delete(WorkspaceModel).where(
            WorkspaceModel.workspace_id == workspace_id,
            WorkspaceModel.user_id == user_id,
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def get_workspace(self, workspace_id: str, user_id: int) -> Workspace | None:
        row = await self.session.scalar(
            select(WorkspaceModel).where(
                WorkspaceModel.workspace_id == workspace_id,
                WorkspaceModel.user_id == user_id,
            )
        )
        return _workspace_to_entity(row) if row is not None else None

    async def list_workspaces(
        self, user_id: int, *, limit: int, offset: int
    ) -> tuple[list[Workspace], int]:
        total = await self.session.scalar(
            select(func.count())
            .select_from(WorkspaceModel)
            .where(WorkspaceModel.user_id == user_id)
        )
        rows = (
            (
                await self.session.execute(
                    select(WorkspaceModel)
                    .where(WorkspaceModel.user_id == user_id)
                    # "Last used" rather than "last edited": a workspace that has
                    # never run anything falls back to its creation time.
                    .order_by(
                        func.coalesce(
                            WorkspaceModel.last_activity_at, WorkspaceModel.created_at
                        ).desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_workspace_to_entity(row) for row in rows], int(total or 0)

    async def count_workspaces(self, user_id: int) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(WorkspaceModel)
            .where(WorkspaceModel.user_id == user_id)
        )
        return int(total or 0)


class SQLRuntimeRepository:
    """Persistence for the single runtime slot of each workspace."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_runtime_for_workspace(self, workspace_id: str) -> WorkspaceRuntime | None:
        row = await self.session.scalar(
            select(WorkspaceRuntimeModel).where(
                WorkspaceRuntimeModel.workspace_id == workspace_id
            )
        )
        return _runtime_to_entity(row) if row is not None else None

    async def get_or_create_runtime(
        self, *, workspace_id: str, provider: str, resource_profile: str
    ) -> WorkspaceRuntime:
        existing = await self.get_runtime_for_workspace(workspace_id)
        if existing is not None:
            return existing

        candidate = WorkspaceRuntime(
            runtime_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            provider=provider,
            resource_profile=resource_profile,
        )
        self.session.add(
            WorkspaceRuntimeModel(
                runtime_id=candidate.runtime_id,
                workspace_id=workspace_id,
                provider=provider,
                state=str(RuntimeState.STOPPED),
                resource_profile=resource_profile,
            )
        )
        try:
            await self.session.commit()
        except IntegrityError:
            # A concurrent start won the insert. Re-read rather than failing:
            # the caller's intent ("this workspace should have a runtime") is
            # already satisfied by the winner's row.
            await self.session.rollback()
            winner = await self.get_runtime_for_workspace(workspace_id)
            if winner is None:  # pragma: no cover - defensive
                raise
            return winner
        return candidate

    async def save_runtime(self, runtime: WorkspaceRuntime) -> None:
        statement = (
            update(WorkspaceRuntimeModel)
            .where(WorkspaceRuntimeModel.workspace_id == runtime.workspace_id)
            .values(
                provider=runtime.provider,
                provider_ref=runtime.provider_ref,
                state=str(runtime.state),
                image_revision_id=runtime.image_revision_id,
                resource_profile=runtime.resource_profile,
                failure_reason=runtime.failure_reason,
                provider_metadata=dict(runtime.provider_metadata),
                started_at=runtime.started_at,
                stopped_at=runtime.stopped_at,
                last_activity_at=runtime.last_activity_at,
            )
        )
        await self.session.execute(statement)
        await self.session.commit()

    async def delete_runtime_for_workspace(self, workspace_id: str) -> bool:
        statement = delete(WorkspaceRuntimeModel).where(
            WorkspaceRuntimeModel.workspace_id == workspace_id
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]


class SQLRuntimeImageRevisionRepository:
    """Persistence for saved environment snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_revision(self, revision: RuntimeImageRevision) -> None:
        self.session.add(
            RuntimeImageRevisionModel(
                revision_id=revision.revision_id,
                workspace_id=revision.workspace_id,
                provider=revision.provider,
                config_fingerprint=revision.config_fingerprint,
                base_image_revision=revision.base_image_revision,
                status=str(revision.status),
                provider_snapshot_ref=revision.provider_snapshot_ref,
                error=revision.error,
                validation_metadata=dict(revision.validation_metadata),
            )
        )
        await self.session.commit()

    async def update_revision(self, revision: RuntimeImageRevision) -> bool:
        statement = (
            update(RuntimeImageRevisionModel)
            .where(
                RuntimeImageRevisionModel.revision_id == revision.revision_id,
                RuntimeImageRevisionModel.workspace_id == revision.workspace_id,
            )
            .values(
                status=str(revision.status),
                provider_snapshot_ref=revision.provider_snapshot_ref,
                error=revision.error,
                validation_metadata=dict(revision.validation_metadata),
                ready_at=revision.ready_at,
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def get_revision(
        self, revision_id: str, workspace_id: str
    ) -> RuntimeImageRevision | None:
        row = await self.session.scalar(
            select(RuntimeImageRevisionModel).where(
                RuntimeImageRevisionModel.revision_id == revision_id,
                RuntimeImageRevisionModel.workspace_id == workspace_id,
            )
        )
        return _revision_to_entity(row) if row is not None else None

    async def find_reusable_revision(
        self,
        workspace_id: str,
        *,
        config_fingerprint: str,
        base_image_revision: str,
    ) -> RuntimeImageRevision | None:
        row = await self.session.scalar(
            select(RuntimeImageRevisionModel)
            .where(
                RuntimeImageRevisionModel.workspace_id == workspace_id,
                RuntimeImageRevisionModel.status == str(ImageRevisionStatus.READY),
                # Both halves of the compatibility check are in the query, so a
                # snapshot built on a replaced base image can never even be a
                # candidate.
                RuntimeImageRevisionModel.config_fingerprint == config_fingerprint,
                RuntimeImageRevisionModel.base_image_revision == base_image_revision,
                RuntimeImageRevisionModel.provider_snapshot_ref.is_not(None),
            )
            .order_by(RuntimeImageRevisionModel.created_at.desc())
            .limit(1)
        )
        return _revision_to_entity(row) if row is not None else None

    async def list_revisions(
        self, workspace_id: str, *, limit: int
    ) -> list[RuntimeImageRevision]:
        rows = (
            (
                await self.session.execute(
                    select(RuntimeImageRevisionModel)
                    .where(RuntimeImageRevisionModel.workspace_id == workspace_id)
                    .order_by(RuntimeImageRevisionModel.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_revision_to_entity(row) for row in rows]

    async def delete_revisions_for_workspace(self, workspace_id: str) -> int:
        result = await self.session.execute(
            delete(RuntimeImageRevisionModel).where(
                RuntimeImageRevisionModel.workspace_id == workspace_id
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_revision(self, revision_id: str, workspace_id: str) -> bool:
        result = await self.session.execute(
            delete(RuntimeImageRevisionModel).where(
                RuntimeImageRevisionModel.revision_id == revision_id,
                RuntimeImageRevisionModel.workspace_id == workspace_id,
            )
        )
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]
