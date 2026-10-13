"""Persistence ports for workspaces, their runtimes and their image revisions.

Split the same way ``workflow_port`` is: a write-only slice for anything that
only creates rows, and the full protocol for the service. Every user-facing read
is scoped by ``user_id`` and that is the interface's job to state — a repository
whose reads are not scoped is one forgotten ``where`` away from showing one
account another's workspaces and, worse, their code.
"""

from typing import Protocol

from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.entities.workspace import Workspace
from src.domain.workspaces.entities.workspace_runtime import WorkspaceRuntime


class WorkspaceWriteRepositoryPort(Protocol):
    async def create_workspace(self, workspace: Workspace) -> None:
        """Insert a workspace."""
        ...

    async def update_workspace(self, workspace: Workspace) -> bool:
        """Persist a changed name/description/state/configuration.

        Returns False when the row is missing or owned by someone else, so the
        caller answers 404 rather than pretending the write landed.
        """
        ...

    async def delete_workspace(self, workspace_id: str, user_id: int) -> bool:
        """Delete a workspace. Returns False when it is missing or not owned."""
        ...


class WorkspaceRepositoryPort(WorkspaceWriteRepositoryPort, Protocol):
    async def get_workspace(self, workspace_id: str, user_id: int) -> Workspace | None:
        """Fetch one workspace owned by ``user_id``, else None."""
        ...

    async def list_workspaces(
        self, user_id: int, *, limit: int, offset: int
    ) -> tuple[list[Workspace], int]:
        """A page of the user's workspaces (newest activity first) and the total."""
        ...

    async def count_workspaces(self, user_id: int) -> int:
        """How many workspaces the user owns. Used to enforce a per-user cap."""
        ...


class RuntimeRepositoryPort(Protocol):
    """Persistence for the single runtime attached to a workspace.

    There is at most one runtime per workspace, so the read is by
    ``workspace_id`` rather than by runtime id: every caller that wants a runtime
    already has the workspace, and looking it up by the disposable runtime id
    would make "my runtime was replaced" escape the model.
    """

    async def get_runtime_for_workspace(self, workspace_id: str) -> WorkspaceRuntime | None:
        """The workspace's runtime, or None when it has never been started."""
        ...

    async def get_or_create_runtime(
        self, *, workspace_id: str, provider: str, resource_profile: str
    ) -> WorkspaceRuntime:
        """The workspace's existing runtime, or a freshly created one.

        A single call rather than "read, then insert if missing" in the service,
        because those two steps are a race: two concurrent starts both see no
        runtime and both insert, and the loser trips the one-runtime-per-workspace
        constraint at commit time — inside whatever the caller had already done.
        The implementation resolves that race itself (insert, and on conflict
        re-read), so the caller cannot get it wrong.
        """
        ...

    async def save_runtime(self, runtime: WorkspaceRuntime) -> None:
        """Persist the runtime's current state. One row per workspace (upsert).

        The row's ``runtime_id`` is authoritative and is never rewritten: it is
        the stable internal id of the runtime *slot* for that workspace, and a
        replacement container is a change of ``provider_ref``, not of identity.
        """
        ...

    async def delete_runtime_for_workspace(self, workspace_id: str) -> bool:
        """Remove the runtime row. Returns False when there was none."""
        ...


class RuntimeImageRevisionRepositoryPort(Protocol):
    """Persistence for saved environment snapshots."""

    async def create_revision(self, revision: RuntimeImageRevision) -> None:
        """Insert a revision in its initial (``PENDING``) state."""
        ...

    async def update_revision(self, revision: RuntimeImageRevision) -> bool:
        """Persist a status/handle change. False when the row is gone."""
        ...

    async def get_revision(
        self, revision_id: str, workspace_id: str
    ) -> RuntimeImageRevision | None:
        """Fetch one revision, scoped to its workspace."""
        ...

    async def find_reusable_revision(
        self,
        workspace_id: str,
        *,
        config_fingerprint: str,
        base_image_revision: str,
    ) -> RuntimeImageRevision | None:
        """The newest ``READY`` revision matching both inputs, or None.

        Both halves are part of the query rather than filtered in the service, so
        a stale base image can never even be a candidate.
        """
        ...

    async def list_revisions(
        self, workspace_id: str, *, limit: int
    ) -> list[RuntimeImageRevision]:
        """Recent revisions for a workspace, newest first."""
        ...

    async def delete_revisions_for_workspace(self, workspace_id: str) -> int:
        """Remove a workspace's revisions. Returns how many were deleted."""
        ...

    async def delete_revision(self, revision_id: str, workspace_id: str) -> bool:
        """Remove one revision, scoped to its workspace.

        Used by the retention policy to retire old snapshots. Scoped by
        ``workspace_id`` even though the id is unique, because an unscoped delete
        reachable from a worker is one wrong argument away from removing another
        workspace's snapshot.
        """
        ...
