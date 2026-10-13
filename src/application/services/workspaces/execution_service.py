"""Submitting and tracking executions.

The API never runs user code, and this service is where that rule is enforced: it
writes a durable row and publishes a *signal*. Everything that actually touches a
runtime happens in the workspace worker, so a request that arrives while the
execution host is down still succeeds — the work simply waits.

Two properties are worth stating because they are the reason for the shapes below:

* **A submit is idempotent when the client supplies a key.** The unique index is
  the real guard; this method resolves the race that follows from it rather than
  pretending a read-then-write is atomic.
* **Cancellation is a status, not a message removal.** A queued execution is
  cancelled by moving the row, and the worker's claim is conditional on
  ``QUEUED`` — so a cancelled execution can never be picked up, without needing
  to reach into the queue.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
import uuid

from src.application.exceptions.workspace_exceptions import (
    PackageInstallNotPermittedError,
    WorkspaceNotFoundError,
    WorkspaceNotUsableError,
)
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.workspaces.entities.execution import (
    MAX_SOURCE_CHARS,
    Execution,
    ExecutionArtifact,
)
from src.domain.workspaces.exceptions import InvalidWorkspaceError
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.project_operation import parse_project_operation

#: Bounds on a submitted run. The timeout ceiling is what keeps one workspace
#: from occupying a runtime indefinitely; the default is short enough that a
#: forgotten interactive run releases the runtime on its own.
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 300
MAX_EXECUTION_TIMEOUT_SECONDS = 3_600
#: A command line is a shell program, so it is bounded separately from a script.
MAX_COMMAND_CHARS = 8_000
#: Concurrent executions per workspace. Beyond this, submits are refused rather
#: than queued forever — a queue that grows without bound is a queue that hides a
#: run-away client.
DEFAULT_MAX_ACTIVE_EXECUTIONS = 5#: How many recent executions the artifacts view scans, and how many artifacts it
#: will return. Artifacts live in a JSON column, so there is no cheap relational
#: query for them; a bounded scan with an explicit limit is the honest trade.
ARTIFACT_SCAN_EXECUTIONS = 50
MAX_ARTIFACTS_RETURNED = 200


@runtime_checkable
class RuntimeCapacityPort(Protocol):
    """The capacity gate an execution asks before it is queued.

    One method on purpose. ``WorkspaceService`` implements it, which keeps the
    disk policy in the layer that owns the runtime provider instead of spreading
    it into the execution pipeline — and lets a test supply a gate that raises
    without standing up a provider.
    """

    async def ensure_capacity(self, workspace_id: str, user_id: int) -> None:
        """Raise when the workspace has no room for more work."""
        ...


class ExecutionNotFoundError(Exception):
    """The execution is missing, or is not owned by the caller."""


class TooManyActiveExecutionsError(Exception):
    """The workspace already has the maximum number of executions in flight."""


class InvalidExecutionRequestError(Exception):
    """The submitted run is not something this system will accept."""


@dataclass(frozen=True)
class ExecutionArtifactRef:
    """One artifact plus the execution that produced it."""

    execution_id: str
    artifact: ExecutionArtifact


class ExecutionService:
    def __init__(
        self,
        *,
        execution_repository,
        workspace_repository,
        queue,
        max_active_executions: int = DEFAULT_MAX_ACTIVE_EXECUTIONS,
        capacity: RuntimeCapacityPort | None = None,
        allows_package_install: bool = True,
    ) -> None:
        self.execution_repository = execution_repository
        self.workspace_repository = workspace_repository
        self.queue = queue
        self.max_active_executions = max_active_executions
        #: Enforces the workspace's disk budget before work is queued. Optional
        #: so a unit test (or a deployment with enforcement off) can omit it; a
        #: missing collaborator means "no capacity gate", never "gate bypassed".
        self.capacity = capacity
        #: Whether the deployment's egress policy can reach a package index.
        #: Defaults to True so nothing changes for a deployment that has egress;
        #: an isolated deployment passes False and dependency installation is
        #: refused with an explanation instead of failing inside the runtime.
        self.allows_package_install = allows_package_install

    # -- submission --------------------------------------------------------

    @staticmethod
    def _validate_request(
        mode: ExecutionMode, source: str, timeout_seconds: int
    ) -> str:
        """Validate a submit, returning the normalised source.

        Validation is per mode because the modes are not interchangeable: a
        project operation is checked against a closed allow-list, a command is
        bounded as a shell program, and a script is bounded as source code. Doing
        it here — not in the adapter, and not only in the request schema — means
        the worker and any future SDK get the same answers.
        """
        text = str(source)
        if not text.strip():
            raise InvalidExecutionRequestError("An execution requires a non-empty source.")
        if "\x00" in text:
            raise InvalidExecutionRequestError("The source may not contain NUL bytes.")
        if not 1 <= timeout_seconds <= MAX_EXECUTION_TIMEOUT_SECONDS:
            raise InvalidExecutionRequestError(
                f"timeout_seconds must be between 1 and {MAX_EXECUTION_TIMEOUT_SECONDS}."
            )
        if mode is ExecutionMode.PROJECT:
            # Raises InvalidWorkspaceError with a specific message; re-raised
            # below as an invalid-request error so the API has one mapping.
            parse_project_operation(text)
            return text.strip()
        if mode is ExecutionMode.COMMAND:
            if len(text) > MAX_COMMAND_CHARS:
                raise InvalidExecutionRequestError(
                    f"A command may be at most {MAX_COMMAND_CHARS} characters."
                )
            return text
        if len(text) > MAX_SOURCE_CHARS:
            raise InvalidExecutionRequestError(
                f"A script may be at most {MAX_SOURCE_CHARS} characters."
            )
        return text

    async def submit(
        self,
        *,
        workspace_id: str,
        user_id: int,
        mode: ExecutionMode,
        source: str,
        timeout_seconds: int = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        idempotency_key: str | None = None,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> Execution:
        workspace = await self.workspace_repository.get_workspace(workspace_id, user_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        if not workspace.accepts_executions():
            raise WorkspaceNotUsableError(workspace_id)

        try:
            validated = self._validate_request(mode, source, timeout_seconds)
        except InvalidWorkspaceError as exc:
            raise InvalidExecutionRequestError(str(exc)) from exc

        # Policy checks before the row is written, so a refusal costs nothing and
        # leaves no trace the user has to clean up.
        if mode is ExecutionMode.PROJECT and not self.allows_package_install:
            # Addressed to the person reading it. The old wording told them to
            # "choose a different egress policy", which is not something a user
            # can do — egress is topology the operator configures. Naming the
            # deployment as the owner is the difference between a dead end and a
            # request someone can act on.
            raise PackageInstallNotPermittedError(
                "This deployment runs code with no outbound network access, so "
                "dependencies cannot be installed or re-resolved. 'uv' project "
                "operations need to reach a package index. Enabling them is a "
                "deployment setting, not something you can change here — ask the "
                "operator to allow package egress, or use a SCRIPT run with the "
                "packages already saved in the workspace environment."
            )
        if self.capacity is not None:
            await self.capacity.ensure_capacity(workspace_id, user_id)

        key = (idempotency_key or "").strip() or None
        if key is not None:
            existing = await self.execution_repository.find_execution_by_idempotency_key(
                workspace_id, key
            )
            if existing is not None:
                return existing

        active = await self.execution_repository.list_active_executions_for_workspace(
            workspace_id
        )
        if len(active) >= self.max_active_executions:
            raise TooManyActiveExecutionsError(
                f"This workspace already has {len(active)} executions in flight."
            )

        execution = Execution(
            execution_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            user_id=user_id,
            mode=mode,
            source=validated,
            idempotency_key=key,
            origin=origin,
        )
        stored = await self.execution_repository.create_execution(execution)
        if stored.execution_id != execution.execution_id:
            # A concurrent submit with the same key won the insert. Its row is the
            # answer for both callers, and nothing further should happen: the
            # winner already bumped the counter and published the message.
            return stored

        workspace.execution_count += 1
        workspace.touch()
        await self.workspace_repository.update_workspace(workspace)

        try:
            await self.queue.publish_execution(execution)
        except Exception as exc:  # noqa: BLE001 - reported as a durable failure
            # The row exists and says QUEUED. Leaving it there would show the user
            # work that will never run, so it is failed explicitly with the reason.
            execution.fail(error="Could not queue the execution for a worker.")
            await self.execution_repository.update_execution(execution)
            raise InvalidExecutionRequestError(
                "The execution could not be queued. Please try again."
            ) from exc

        return execution

    # -- reads -------------------------------------------------------------

    async def get(self, execution_id: str, user_id: int) -> Execution:
        execution = await self.execution_repository.get_execution_for_user(
            execution_id, user_id
        )
        if execution is None:
            raise ExecutionNotFoundError(execution_id)
        return execution

    async def list_for_workspace(
        self, *, workspace_id: str, user_id: int, limit: int, offset: int
    ) -> tuple[list[Execution], int]:
        workspace = await self.workspace_repository.get_workspace(workspace_id, user_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        return await self.execution_repository.list_executions_for_workspace(
            workspace_id, limit=limit, offset=offset
        )

    async def cancel(self, execution_id: str, user_id: int) -> Execution:
        """Cancel an execution, idempotently.

        A queued run is cancelled by this row change alone: the worker's claim is
        ``WHERE status = 'QUEUED'``, so a cancelled row simply never matches. A
        running run is cancelled here too, and the worker treats a failed
        transition on completion as "the user already cancelled" rather than as an
        error — see the worker's terminal-handling comment.
        """
        execution = await self.get(execution_id, user_id)
        if execution.cancel():
            await self.execution_repository.update_execution(execution)
        return execution

    async def get_artifact(
        self, execution_id: str, user_id: int, path: str
    ) -> ExecutionArtifact | None:
        """One artifact of an owned execution, or ``None`` when absent.

        Ownership is the execution's, checked through the same read the rest of
        the API uses — an artifact is not a separate resource with its own
        permissions, so it must not grow its own lookup. ``None`` covers both
        "no such artifact" and "not stored", because the caller answers the same
        way for either (nothing to download) and a different answer would make
        the two distinguishable from outside.
        """
        execution = await self.get(execution_id, user_id)
        for artifact in execution.artifacts:
            if artifact.path == path and artifact.object_key:
                return artifact
        return None

    async def list_artifacts(
        self, *, workspace_id: str, user_id: int
    ) -> list[ExecutionArtifactRef]:
        """Artifacts from the most recent executions.

        Bounded by :data:`ARTIFACT_SCAN_EXECUTIONS` because artifacts are stored
        inside each execution's JSON column: a full scan would have to read every
        row. The API reports the bound alongside the results rather than implying
        the list is exhaustive.
        """
        workspace = await self.workspace_repository.get_workspace(workspace_id, user_id)
        if workspace is None:
            raise WorkspaceNotFoundError(workspace_id)
        executions, _total = await self.execution_repository.list_executions_for_workspace(
            workspace_id, limit=ARTIFACT_SCAN_EXECUTIONS, offset=0
        )
        artifacts: list[ExecutionArtifactRef] = []
        for execution in executions:
            for artifact in execution.artifacts:
                artifacts.append(
                    ExecutionArtifactRef(execution_id=execution.execution_id, artifact=artifact)
                )
                if len(artifacts) >= MAX_ARTIFACTS_RETURNED:
                    return artifacts
        return artifacts
