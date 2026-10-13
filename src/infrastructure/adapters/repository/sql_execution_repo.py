"""SQL adapter for executions.

The one thing this adapter must get right is :meth:`SQLExecutionRepository.claim_execution`.
Everything else is CRUD, but the claim is what stops a redelivered queue message
(the reclaim path after a crashed worker) from running the same work twice: it is
a single conditional ``UPDATE``, so exactly one caller can observe ``rowcount == 1``.
A read followed by a write would look correct in every test and fail exactly when
two workers race.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.conversions.value_object.job_origin import coerce_job_origin
from src.domain.workspaces.entities.execution import Execution, ExecutionArtifact
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import ExecutionStatus
from src.infrastructure.adapters.repository.sql_workspace_repo import aware
from src.infrastructure.database.models.workspace import ExecutionModel

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _artifacts_to_json(artifacts: list[ExecutionArtifact]) -> list[dict[str, Any]]:
    return [artifact.to_dict() for artifact in artifacts]


def _artifacts_from_json(raw: Any) -> list[ExecutionArtifact]:
    """Rebuild artifacts, skipping entries that cannot be rendered.

    A malformed entry is dropped rather than raising: this column is written by
    the worker, and one bad entry must not make the whole execution unreadable.
    """
    if not isinstance(raw, list):
        return []
    artifacts: list[ExecutionArtifact] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        object_key = item.get("object_key")
        artifacts.append(
            ExecutionArtifact(
                path=str(item["path"]),
                size_bytes=int(item.get("size_bytes") or 0),
                object_key=str(object_key) if object_key else None,
                content_type=str(item.get("content_type") or _DEFAULT_CONTENT_TYPE),
            )
        )
    return artifacts


def _execution_to_entity(row: ExecutionModel) -> Execution:
    return Execution(
        execution_id=row.execution_id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        mode=ExecutionMode(row.mode),
        source=row.source,
        status=ExecutionStatus(row.status),
        runtime_id=row.runtime_id,
        exit_code=row.exit_code,
        stdout_excerpt=row.stdout_excerpt or "",
        stderr_excerpt=row.stderr_excerpt or "",
        stdout_ref=row.stdout_ref,
        stderr_ref=row.stderr_ref,
        stdout_truncated=bool(row.stdout_truncated),
        stderr_truncated=bool(row.stderr_truncated),
        error=row.error,
        artifacts=_artifacts_from_json(row.artifacts),
        duration_ms=row.duration_ms or 0,
        memory_peak_bytes=row.memory_peak_bytes or 0,
        idempotency_key=row.idempotency_key,
        origin=coerce_job_origin(row.origin),
        created_at=aware(row.created_at),
        started_at=aware(row.started_at),
        completed_at=aware(row.completed_at),
    )


class SQLExecutionRepository:
    """Execution persistence for both the API and the workspace worker."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_execution(self, execution: Execution) -> Execution:
        self.session.add(
            ExecutionModel(
                execution_id=execution.execution_id,
                workspace_id=execution.workspace_id,
                user_id=execution.user_id,
                mode=str(execution.mode),
                source=execution.source,
                status=str(execution.status),
                idempotency_key=execution.idempotency_key,
                origin=str(execution.origin),
            )
        )
        try:
            await self.session.commit()
        except IntegrityError:
            # An idempotency-key collision, not a bug: the caller asked for "at
            # most one execution per key". The winner's row is the answer for
            # both callers, so it is returned rather than raising.
            await self.session.rollback()
            key = execution.idempotency_key
            if key is None:
                raise
            existing = await self.find_execution_by_idempotency_key(
                execution.workspace_id, key
            )
            if existing is None:  # pragma: no cover - defensive
                raise
            return existing
        return execution

    async def update_execution(self, execution: Execution) -> bool:
        statement = (
            update(ExecutionModel)
            .where(ExecutionModel.execution_id == execution.execution_id)
            .values(
                status=str(execution.status),
                runtime_id=execution.runtime_id,
                exit_code=execution.exit_code,
                stdout_excerpt=execution.stdout_excerpt,
                stderr_excerpt=execution.stderr_excerpt,
                stdout_ref=execution.stdout_ref,
                stderr_ref=execution.stderr_ref,
                stdout_truncated=execution.stdout_truncated,
                stderr_truncated=execution.stderr_truncated,
                error=execution.error,
                artifacts=_artifacts_to_json(execution.artifacts),
                duration_ms=execution.duration_ms,
                memory_peak_bytes=execution.memory_peak_bytes,
                started_at=execution.started_at,
                completed_at=execution.completed_at,
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def get_execution(self, execution_id: str) -> Execution | None:
        row = await self.session.scalar(
            select(ExecutionModel).where(ExecutionModel.execution_id == execution_id)
        )
        return _execution_to_entity(row) if row is not None else None

    async def get_execution_for_user(
        self, execution_id: str, user_id: int
    ) -> Execution | None:
        row = await self.session.scalar(
            select(ExecutionModel).where(
                ExecutionModel.execution_id == execution_id,
                ExecutionModel.user_id == user_id,
            )
        )
        return _execution_to_entity(row) if row is not None else None

    async def list_executions_for_workspace(
        self, workspace_id: str, *, limit: int, offset: int
    ) -> tuple[list[Execution], int]:
        total = await self.session.scalar(
            select(func.count())
            .select_from(ExecutionModel)
            .where(ExecutionModel.workspace_id == workspace_id)
        )
        rows = (
            (
                await self.session.execute(
                    select(ExecutionModel)
                    .where(ExecutionModel.workspace_id == workspace_id)
                    .order_by(ExecutionModel.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return [_execution_to_entity(row) for row in rows], int(total or 0)

    async def find_execution_by_idempotency_key(
        self, workspace_id: str, idempotency_key: str
    ) -> Execution | None:
        row = await self.session.scalar(
            select(ExecutionModel).where(
                ExecutionModel.workspace_id == workspace_id,
                ExecutionModel.idempotency_key == idempotency_key,
            )
        )
        return _execution_to_entity(row) if row is not None else None

    async def claim_execution(self, execution_id: str, runtime_id: str) -> bool:
        """Atomically take ownership of a queued execution.

        ``WHERE status = 'QUEUED'`` is the whole mechanism: the database, not the
        worker, decides who wins.
        """
        statement = (
            update(ExecutionModel)
            .where(
                ExecutionModel.execution_id == execution_id,
                ExecutionModel.status == str(ExecutionStatus.QUEUED),
            )
            .values(
                status=str(ExecutionStatus.RUNNING),
                runtime_id=runtime_id,
                started_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def list_active_executions_for_workspace(
        self, workspace_id: str
    ) -> list[Execution]:
        rows = (
            (
                await self.session.execute(
                    select(ExecutionModel).where(
                        ExecutionModel.workspace_id == workspace_id,
                        ExecutionModel.status.in_(
                            [
                                str(ExecutionStatus.QUEUED),
                                str(ExecutionStatus.RUNNING),
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_execution_to_entity(row) for row in rows]

    async def delete_executions_for_workspace(self, workspace_id: str) -> int:
        result = await self.session.execute(
            delete(ExecutionModel).where(ExecutionModel.workspace_id == workspace_id)
        )
        await self.session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
