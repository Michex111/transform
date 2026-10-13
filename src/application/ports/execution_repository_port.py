"""Persistence port for executions.

The one asymmetry worth calling out: ``get_execution`` is **not** owner-scoped, and
exists for the worker, which runs with no user context. The API must use
``get_execution_for_user``. Both names say which is which, because an unscoped
read reached from a request handler is an access-control bug that no compiler
will catch.
"""

from typing import Protocol

from src.domain.workspaces.entities.execution import Execution


class ExecutionRepositoryPort(Protocol):
    async def create_execution(self, execution: Execution) -> Execution:
        """Insert a new execution, or return the existing one on a key conflict.

        Returns the execution that is now stored. When a client-supplied
        idempotency key already exists for the workspace, that existing row is
        returned instead of raising: the caller asked for "at most one", and the
        adapter is the only layer that owns the session needed to resolve the
        unique-index conflict and re-read.
        """
        ...

    async def update_execution(self, execution: Execution) -> bool:
        """Persist a status/output change. False when the row is gone."""
        ...

    async def get_execution(self, execution_id: str) -> Execution | None:
        """Fetch by id with NO ownership check. For the worker only."""
        ...

    async def get_execution_for_user(
        self, execution_id: str, user_id: int
    ) -> Execution | None:
        """Fetch by id, scoped to its owner. The only read the API may use."""
        ...

    async def list_executions_for_workspace(
        self, workspace_id: str, *, limit: int, offset: int
    ) -> tuple[list[Execution], int]:
        """A page of a workspace's executions (newest first) and the total."""
        ...

    async def find_execution_by_idempotency_key(
        self, workspace_id: str, idempotency_key: str
    ) -> Execution | None:
        """The existing execution for a client-supplied key, or None."""
        ...

    async def claim_execution(self, execution_id: str, runtime_id: str) -> bool:
        """Atomically move ``QUEUED → RUNNING`` for one worker.

        Returns True for the single caller that won the claim. This is what makes
        a redelivered queue message (a reclaim after a crashed worker) run the
        work once instead of twice, so it must be a conditional update in the
        database — not a read followed by a write.
        """
        ...

    async def list_active_executions_for_workspace(self, workspace_id: str) -> list[Execution]:
        """Executions in ``QUEUED`` or ``RUNNING``. Used before a stop or delete."""
        ...

    async def delete_executions_for_workspace(self, workspace_id: str) -> int:
        """Remove a workspace's execution history. Returns the number deleted."""
        ...
