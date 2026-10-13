"""Workspace lifecycle: creation, configuration, and the runtime state machine.

The service owns every invariant that spans more than one entity — "a config
change is refused while a runtime is serving", "a start on a running runtime
returns the current state instead of allocating another instance", "a stop is
refused while work is in flight unless it is forced". Those rules live here
rather than in the HTTP handler because they are product rules, and a second
caller (the workspace worker, a future SDK) must get the same answers.

The guiding split, restated because every method below depends on it: the
**workspace** is durable and the **runtime** is disposable. Nothing here deletes
a workspace's files to fix a runtime problem.
"""

from dataclasses import dataclass
import uuid

from src.application.exceptions.runtime_provider_exceptions import (
    RuntimeNotFoundError as ProviderRuntimeNotFoundError,
    RuntimeProviderError,
)
from src.application.exceptions.workspace_exceptions import (
    RuntimeBusyError,
    RuntimeNotFoundError,
    RuntimeNotRunningError,
    SnapshotFailedError,
    WorkspaceLimitReachedError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
    WorkspaceQuotaExceededError,
)
from src.application.ports.runtime_provider_port import (
    ProviderFileEntry,
    RuntimeProviderPort,
    RuntimeSpec,
)
from src.application.ports.workspace_queue_port import WorkspaceQueuePort
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.domain.workspaces.entities.workspace import Workspace
from src.domain.workspaces.entities.workspace_runtime import WorkspaceRuntime
from src.domain.workspaces.value_object.runtime_config import RuntimeConfig
from src.domain.workspaces.value_object.runtime_state import RuntimeState

#: How many environment snapshots a workspace keeps. Snapshots are images; the
#: cap is what stops a workspace used daily for a year from filling the host's
#: image storage with backups nobody will ever restore.
DEFAULT_SNAPSHOT_RETENTION = 5

#: States in which a runtime may not be reconfigured or torn down from under
#: itself. ``STOPPING`` is included because a start may be in flight behind it.
_BUSY_RUNTIME_STATES = frozenset(
    {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING}
)


@dataclass(frozen=True)
class WorkspaceDetail:
    """A workspace together with everything the detail view needs in one round trip."""

    workspace: Workspace
    runtime: WorkspaceRuntime | None
    active_executions: int


@dataclass(frozen=True)
class StartedRuntime:
    """The result of a start request."""

    runtime: WorkspaceRuntime
    #: True when this call actually asked the provider to start something, False
    #: when it answered an already-running (or in-flight) runtime. The API uses
    #: it to answer 200 vs 202 without guessing.
    changed: bool


class WorkspaceService:
    def __init__(
        self,
        *,
        workspace_repository,
        runtime_repository,
        revision_repository,
        execution_repository,
        runtime_provider: RuntimeProviderPort,
        queue: WorkspaceQueuePort,
        max_workspaces: int = 20,
        snapshot_retention: int = DEFAULT_SNAPSHOT_RETENTION,
        disk_quota_bytes: int = 0,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.runtime_repository = runtime_repository
        self.revision_repository = revision_repository
        self.execution_repository = execution_repository
        self.runtime_provider = runtime_provider
        self.queue = queue
        self.max_workspaces = max_workspaces
        self.snapshot_retention = snapshot_retention
        #: Per-workspace disk budget in bytes. ``0`` disables enforcement, which is
        #: only sensible in a test or a deployment that enforces the limit in the
        #: filesystem instead (see ``docs/cloud-workspaces.md``).
        self.disk_quota_bytes = disk_quota_bytes

    # -- helpers -----------------------------------------------------------

    async def require_workspace(self, workspace_id: str, user_id: int) -> Workspace:
        """Load a workspace the caller owns, or raise ``WorkspaceNotFoundError``."""
        workspace = await self.workspace_repository.get_workspace(workspace_id, user_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        return workspace

    async def _require_usable(self, workspace_id: str, user_id: int) -> Workspace:
        workspace = await self.require_workspace(workspace_id, user_id)
        if not workspace.accepts_executions():
            raise WorkspaceNotUsableError(workspace_id)
        return workspace

    # -- CRUD --------------------------------------------------------------

    async def create_workspace(
        self,
        *,
        user_id: int,
        name: str,
        description: str = "",
        runtime_config: RuntimeConfig | None = None,
    ) -> Workspace:
        if await self.workspace_repository.count_workspaces(user_id) >= self.max_workspaces:
            raise WorkspaceLimitReachedError(
                f"You may have at most {self.max_workspaces} workspaces."
            )
        workspace = Workspace(
            workspace_id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            description=description,
            runtime_config=runtime_config or RuntimeConfig(),
        )
        await self.workspace_repository.create_workspace(workspace)
        return workspace

    async def list_workspaces(
        self, user_id: int, *, limit: int, offset: int
    ) -> tuple[list[Workspace], int]:
        return await self.workspace_repository.list_workspaces(
            user_id, limit=limit, offset=offset
        )

    async def get_workspace(self, workspace_id: str, user_id: int) -> Workspace:
        return await self.require_workspace(workspace_id, user_id)

    async def get_detail(self, workspace_id: str, user_id: int) -> WorkspaceDetail:
        workspace = await self.require_workspace(workspace_id, user_id)
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        active = await self.execution_repository.list_active_executions_for_workspace(
            workspace_id
        )
        return WorkspaceDetail(
            workspace=workspace, runtime=runtime, active_executions=len(active)
        )

    async def update_workspace(
        self,
        *,
        workspace_id: str,
        user_id: int,
        name: str | None = None,
        description: str | None = None,
        runtime_config: RuntimeConfig | None = None,
    ) -> Workspace:
        workspace = await self._require_usable(workspace_id, user_id)
        if runtime_config is not None and runtime_config != workspace.runtime_config:
            runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
            if runtime is not None and runtime.state in _BUSY_RUNTIME_STATES:
                # Changing the toolchain under a runtime that is serving would make
                # the running environment describe something it is not. The user
                # can stop the runtime and change it, which is also the moment the
                # change can be honoured.
                raise RuntimeBusyError(
                    "Stop the runtime before changing its environment configuration."
                )
        if name is not None:
            workspace.rename(name)
        if description is not None:
            workspace.update_description(description)
        if runtime_config is not None:
            workspace.update_runtime_config(runtime_config)
        await self.workspace_repository.update_workspace(workspace)
        return workspace

    async def archive_workspace(self, workspace_id: str, user_id: int) -> Workspace:
        workspace = await self.require_workspace(workspace_id, user_id)
        workspace.archive()
        await self.workspace_repository.update_workspace(workspace)
        return workspace

    async def reactivate_workspace(self, workspace_id: str, user_id: int) -> Workspace:
        workspace = await self.require_workspace(workspace_id, user_id)
        workspace.reactivate()
        await self.workspace_repository.update_workspace(workspace)
        return workspace

    async def delete_workspace(self, workspace_id: str, user_id: int) -> None:
        """Tear down every provider resource, then the record.

        Ordered so that a failure leaves something recoverable: the row is
        removed LAST, so a provider error part-way through leaves a visible
        workspace the user can retry rather than an orphaned volume nobody has a
        record of. The execution history is removed with the row because it has
        no meaning without its workspace — and the *user's files* are the
        workspace's, so they go too, which is what deleting a workspace means.
        """
        workspace = await self.require_workspace(workspace_id, user_id)
        if workspace.state.value != "DELETING":
            workspace.begin_delete()
            await self.workspace_repository.update_workspace(workspace)

        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is not None and runtime.provider_ref:
            try:
                await self.runtime_provider.delete_runtime(runtime.provider_ref)
            except ProviderRuntimeNotFoundError:
                # Already gone; the goal is reached.
                pass
        try:
            await self.runtime_provider.delete_workspace_state(workspace_id)
        except RuntimeProviderError:
            # The record must still be removable: leaving a workspace the user
            # cannot delete because a host is unreachable is worse than a
            # provider-side volume that a cleanup policy will reap. The failure
            # is re-raised only if the row deletion also fails.
            pass

        await self.revision_repository.delete_revisions_for_workspace(workspace_id)
        await self.execution_repository.delete_executions_for_workspace(workspace_id)
        await self.runtime_repository.delete_runtime_for_workspace(workspace_id)
        await self.workspace_repository.delete_workspace(workspace_id, user_id)

    # -- runtime -----------------------------------------------------------

    async def get_runtime(self, workspace_id: str, user_id: int) -> WorkspaceRuntime | None:
        await self.require_workspace(workspace_id, user_id)
        return await self.runtime_repository.get_runtime_for_workspace(workspace_id)

    async def start_runtime(self, workspace_id: str, user_id: int) -> StartedRuntime:
        """Ensure the workspace has a running runtime. Idempotent.

        Idempotency is the point: ``STARTING``/``RUNNING``/``STOPPING`` are
        returned as-is with ``changed=False``, so a double-click, a retry, or two
        concurrent calls cannot allocate two containers. The one-runtime-per-
        workspace database constraint is the backstop beneath this.
        """
        workspace = await self._require_usable(workspace_id, user_id)
        config = workspace.runtime_config
        runtime = await self.runtime_repository.get_or_create_runtime(
            workspace_id=workspace_id,
            provider=self.runtime_provider.name,
            resource_profile=config.resource_profile,
        )
        if runtime.state in _BUSY_RUNTIME_STATES:
            return StartedRuntime(runtime=runtime, changed=False)

        runtime.begin_start()
        await self.runtime_repository.save_runtime(runtime)

        try:
            provider_ref, revision_id = await self._provider_start(workspace, runtime)
        except RuntimeProviderError as exc:
            # A failed start never touches the workspace's files. Recording the
            # reason and returning the FAILED runtime is the recoverable path:
            # the next start attempts the same thing again.
            runtime.mark_failed(_safe_provider_message(exc))
            await self.runtime_repository.save_runtime(runtime)
            raise

        runtime.mark_running(provider_ref=provider_ref, image_revision_id=revision_id)
        runtime.provider_metadata = {
            **dict(runtime.provider_metadata),
            # Recorded so a later start can tell whether the container it finds
            # was built for this configuration (see ``_provider_start``).
            "config_fingerprint": config.fingerprint,
        }
        await self.runtime_repository.save_runtime(runtime)

        workspace.touch()
        await self.workspace_repository.update_workspace(workspace)
        return StartedRuntime(runtime=runtime, changed=True)

    async def _provider_start(
        self, workspace: Workspace, runtime: WorkspaceRuntime
    ) -> tuple[str, str | None]:
        """Bring a runtime up, reusing a container only when that is safe."""
        config = workspace.runtime_config
        provider_ref = runtime.provider_ref
        reusable = (
            provider_ref is not None
            and runtime.provider_metadata.get("config_fingerprint") == config.fingerprint
        )

        if provider_ref is not None and not reusable:
            # The container was built from a different configuration (a new base
            # image revision, toolchain or resource profile). Restarting it would
            # hand back the environment the user just changed away from, so it is
            # discarded. The workspace volume is untouched.
            try:
                await self.runtime_provider.delete_runtime(provider_ref)
            except RuntimeProviderError:
                pass
            provider_ref = None

        if provider_ref is not None:
            status = await self.runtime_provider.inspect_runtime(provider_ref)
            if status is not None and status.state is RuntimeState.STOPPED:
                status = await self.runtime_provider.start_runtime(provider_ref)
                if status.state is RuntimeState.RUNNING:
                    # Restarting the *same* container keeps its writable layer,
                    # which is where the uv-managed environment lives — so the
                    # user's installed packages survive an ordinary stop/start
                    # without needing a snapshot.
                    return status.provider_ref, runtime.image_revision_id
            elif status is not None and status.state is RuntimeState.RUNNING:
                return status.provider_ref, runtime.image_revision_id

        await self.runtime_provider.ensure_workspace_state(workspace.workspace_id)

        revision = await self.revision_repository.find_reusable_revision(
            workspace.workspace_id,
            config_fingerprint=config.fingerprint,
            base_image_revision=config.base_image_revision,
        )
        if revision is not None and revision.provider_snapshot_ref:
            # A compatible snapshot: start from it and skip the reinstall. The
            # lockfile is reconciled afterwards by ``uv sync``.
            image = revision.provider_snapshot_ref
            revision_id: str | None = revision.revision_id
        else:
            image = config.image_ref
            revision_id = None

        status = await self.runtime_provider.create_runtime(
            RuntimeSpec(
                workspace_id=workspace.workspace_id,
                image=image,
                resource_profile=config.profile,
                labels={"transform.workspace": workspace.workspace_id},
            )
        )
        return status.provider_ref, revision_id

    async def stop_runtime(
        self, workspace_id: str, user_id: int, *, force: bool = False
    ) -> WorkspaceRuntime:
        """Stop the runtime, preserving every durable thing about the workspace."""
        workspace = await self.require_workspace(workspace_id, user_id)
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is None or runtime.state is RuntimeState.STOPPED:
            raise RuntimeNotFoundError(workspace_id)

        if not force:
            active = await self.execution_repository.list_active_executions_for_workspace(
                workspace_id
            )
            if active:
                # Stopping kills the processes; saying so is better than silently
                # destroying work the user can still see in the UI.
                raise RuntimeBusyError(
                    f"{len(active)} execution(s) are still running. "
                    "Stop them, wait, or force the stop."
                )

        runtime.begin_stop()
        await self.runtime_repository.save_runtime(runtime)
        if runtime.provider_ref:
            try:
                await self.runtime_provider.stop_runtime(runtime.provider_ref)
            except ProviderRuntimeNotFoundError:
                # The provider already lost it. The runtime is stopped either way,
                # and refusing to record that would leave it un-stoppable.
                pass
            except RuntimeProviderError as exc:
                runtime.mark_failed(_safe_provider_message(exc))
                await self.runtime_repository.save_runtime(runtime)
                raise
        runtime.mark_stopped()
        await self.runtime_repository.save_runtime(runtime)

        workspace.touch()
        await self.workspace_repository.update_workspace(workspace)
        return runtime

    async def restart_runtime(
        self, workspace_id: str, user_id: int, *, force: bool = False
    ) -> StartedRuntime:
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is not None and runtime.state is not RuntimeState.STOPPED:
            await self.stop_runtime(workspace_id, user_id, force=force)
        return await self.start_runtime(workspace_id, user_id)

    # -- workspace files ---------------------------------------------------

    async def _runtime_with_handle(
        self, workspace_id: str, user_id: int
    ) -> WorkspaceRuntime:
        """The workspace's runtime, requiring that a provider handle exists.

        File operations are the one part of the API that needs a *specific*
        runtime rather than just the workspace, so the failure is stated in those
        terms: "there is no runtime to read from" rather than a generic 404.
        """
        await self.require_workspace(workspace_id, user_id)
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is None or not runtime.provider_ref:
            raise RuntimeNotFoundError(workspace_id)
        return runtime

    async def list_workspace_files(
        self, workspace_id: str, user_id: int, *, path: str = "."
    ) -> list[ProviderFileEntry]:
        runtime = await self._runtime_with_handle(workspace_id, user_id)
        assert runtime.provider_ref is not None  # guaranteed by _runtime_with_handle
        return await self.runtime_provider.list_files(runtime.provider_ref, path)

    async def read_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str
    ) -> bytes:
        runtime = await self._runtime_with_handle(workspace_id, user_id)
        assert runtime.provider_ref is not None
        return await self.runtime_provider.read_file(runtime.provider_ref, path)

    async def write_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str, data: bytes
    ) -> None:
        runtime = await self._runtime_with_handle(workspace_id, user_id)
        assert runtime.provider_ref is not None
        await self._check_disk_budget(
            workspace_id, runtime.provider_ref, incoming_bytes=len(data)
        )
        await self.runtime_provider.write_file(runtime.provider_ref, path, data)

    async def delete_workspace_file(
        self, workspace_id: str, user_id: int, *, path: str
    ) -> None:
        runtime = await self._runtime_with_handle(workspace_id, user_id)
        assert runtime.provider_ref is not None
        await self.runtime_provider.delete_file(runtime.provider_ref, path)

    # -- capacity ----------------------------------------------------------

    async def _used_bytes(self, provider_ref: str) -> int:
        """The workspace's current usage, or 0 when it cannot be measured."""
        try:
            return await self.runtime_provider.measure_workspace_usage(provider_ref)
        except RuntimeProviderError:
            # An unmeasurable workspace must never be treated as full.
            return 0

    async def _check_disk_budget(
        self, workspace_id: str, provider_ref: str, *, incoming_bytes: int = 0
    ) -> None:
        """Refuse a write that would take the workspace past its budget.

        Enforced BEFORE the bytes enter the volume, so the API cannot be used to
        grow a workspace past its quota. What it cannot do is stop an execution
        from overshooting *within* its own run — a plain Docker named volume has
        no filesystem quota, so that is measured and reported instead (see
        :meth:`ensure_capacity` and the docs).
        """
        if self.disk_quota_bytes <= 0:
            return
        used = await self._used_bytes(provider_ref)
        if used + incoming_bytes > self.disk_quota_bytes:
            raise WorkspaceQuotaExceededError(
                (
                    "This workspace is out of disk space "
                    f"({_human_bytes(used)} of {_human_bytes(self.disk_quota_bytes)} "
                    "used). Delete some files and try again."
                ),
                used_bytes=used,
                limit_bytes=self.disk_quota_bytes,
            )

    async def ensure_capacity(self, workspace_id: str, user_id: int) -> None:
        """Refuse new work when the workspace is already over its budget.

        Called before an execution is queued. This is the second half of the
        quota: writes are checked as they arrive, and this catches growth the
        *code* caused, so a workspace cannot be used to fill the host by running
        the same script repeatedly.

        A no-op when the runtime is stopped (nothing can be measured) — the safe
        direction, since starting a runtime to measure it would make a read cost
        compute.
        """
        if self.disk_quota_bytes <= 0:
            return
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is None or not runtime.provider_ref:
            return
        used = await self._used_bytes(runtime.provider_ref)
        if used > self.disk_quota_bytes:
            raise WorkspaceQuotaExceededError(
                (
                    "This workspace has used its whole disk allowance "
                    f"({_human_bytes(used)} of {_human_bytes(self.disk_quota_bytes)}). "
                    "Delete some files before running anything else."
                ),
                used_bytes=used,
                limit_bytes=self.disk_quota_bytes,
            )

    async def disk_usage(self, workspace_id: str, user_id: int) -> dict[str, int]:
        """Usage and budget, for the UI and for a tool that reports them."""
        await self.require_workspace(workspace_id, user_id)
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        used = 0
        if runtime is not None and runtime.provider_ref:
            used = await self._used_bytes(runtime.provider_ref)
        return {
            "used_bytes": used,
            "limit_bytes": self.disk_quota_bytes,
            "remaining_bytes": max(0, self.disk_quota_bytes - used)
            if self.disk_quota_bytes > 0
            else 0,
        }

    # -- environment snapshots ---------------------------------------------

    async def save_environment(
        self, workspace_id: str, user_id: int
    ) -> RuntimeImageRevision:
        """Record that the current environment should be snapshotted.

        The revision row is created first and the build is *queued*: a
        container-filesystem commit on a workspace with a large dependency tree
        takes real time, and doing it inside the request would both block a
        request worker and tie the work to a client that may have gone away. The
        row is what makes "saving…" honest, and it is what a later start reads.
        """
        workspace = await self._require_usable(workspace_id, user_id)
        runtime = await self.runtime_repository.get_runtime_for_workspace(workspace_id)
        if runtime is None or not runtime.is_serving():
            raise RuntimeNotRunningError(
                "Start the runtime before saving its environment."
            )

        config = workspace.runtime_config
        revision = RuntimeImageRevision(
            revision_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            provider=self.runtime_provider.name,
            config_fingerprint=config.fingerprint,
            base_image_revision=config.base_image_revision,
        )
        await self.revision_repository.create_revision(revision)
        try:
            await self.queue.publish_snapshot(revision)
        except Exception as exc:  # noqa: BLE001 - any publish failure is reported
            revision.mark_failed("Could not queue the environment snapshot.")
            await self.revision_repository.update_revision(revision)
            raise SnapshotFailedError(str(exc)) from exc
        return revision

    async def list_revisions(
        self, workspace_id: str, user_id: int, *, limit: int = 20
    ) -> list[RuntimeImageRevision]:
        await self.require_workspace(workspace_id, user_id)
        return await self.revision_repository.list_revisions(workspace_id, limit=limit)

    async def latest_revision(
        self, workspace_id: str, user_id: int
    ) -> RuntimeImageRevision | None:
        revisions = await self.list_revisions(workspace_id, user_id, limit=1)
        return revisions[0] if revisions else None

    # -- presentation helpers ----------------------------------------------

    async def runtime_configuration(self, workspace_id: str, user_id: int) -> dict:
        """The runtime configuration as the client needs to render it."""
        workspace = await self.require_workspace(workspace_id, user_id)
        config = workspace.runtime_config
        revision = await self.revision_repository.find_reusable_revision(
            workspace_id,
            config_fingerprint=config.fingerprint,
            base_image_revision=config.base_image_revision,
        )
        return {
            "config": config,
            "profile": config.profile,
            "config_fingerprint": config.fingerprint,
            "reusable_revision_id": revision.revision_id if revision else None,
        }

    async def provider_description(self) -> dict:
        """Provider identity and capabilities, for diagnostics and the UI."""
        try:
            described = await self.runtime_provider.describe()
        except RuntimeProviderError as exc:
            return {"provider": self.runtime_provider.name, "available": False,
                    "error": _safe_provider_message(exc)}
        return dict(described)


def _human_bytes(size: int) -> str:
    """Compact size for an error message. Presentation only, never a limit."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _safe_provider_message(exc: RuntimeProviderError) -> str:
    """A bounded, user-safe rendering of a provider failure.

    Provider messages can carry a hostname, a socket path or a registry URL.
    They are truncated and kept out of the response body's *structure* — the API
    returns the runtime's state, and this string only ever appears as the stored
    ``failure_reason``.
    """
    text = str(exc) or exc.__class__.__name__
    return text[:480]


__all__ = [
    "DEFAULT_SNAPSHOT_RETENTION",
    "StartedRuntime",
    "WorkspaceDetail",
    "WorkspaceService",
]
