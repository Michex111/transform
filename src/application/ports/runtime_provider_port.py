"""The provider-neutral runtime port.

This is the only interface the application layer has to a compute provider. It is
deliberately expressed in *workspace* terms — a workspace id, a resource profile,
a relative file path — and never in provider terms: no image ids, no container
names, no SDK types cross this boundary. ``provider_ref`` is an opaque string the
adapter mints and interprets; everything else is domain vocabulary.

Adapting the operations to what a provider can actually do (rather than forcing
unsupported semantics onto it) is the adapter's job. Where a provider cannot do
something — there is no partial "rename a snapshot" here, for instance — the
capability is simply absent from the port rather than offered as a stub that
lies.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.resource_profile import ResourceProfile
from src.domain.workspaces.value_object.runtime_state import RuntimeState

#: The working directory inside every runtime, and the mount point of the
#: workspace's durable volume. Fixed on purpose: a provider that changed it
#: would change the meaning of every stored file path.
WORKSPACE_ROOT = "/workspace"

#: Dependencies live OUTSIDE the durable volume so that a container-filesystem
#: snapshot captures them (see the plan: ``docker commit`` does not capture
#: volume mounts). This is the single design decision that makes environment
#: snapshots meaningful.
VENV_PATH = "/opt/venv"
UV_CACHE_PATH = "/opt/uv-cache"


@dataclass(frozen=True)
class RuntimeSpec:
    """What to create, in domain terms."""

    workspace_id: str
    #: Base image reference, or a workspace snapshot reference the adapter
    #: produced earlier. The adapter decides how to interpret it.
    image: str
    resource_profile: ResourceProfile
    #: Non-secret environment for the runtime (e.g. ``UV_PROJECT_ENVIRONMENT``).
    #: Secrets are injected per-execution, never here, and never snapshotted.
    environment: Mapping[str, str] = field(default_factory=dict)
    #: Opaque labels/tags for provider-side bookkeeping and cleanup.
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRuntimeStatus:
    """What the provider says about a runtime right now."""

    provider_ref: str
    state: RuntimeState
    image: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRequest:
    """One bounded piece of work to run inside a runtime."""

    mode: ExecutionMode
    #: SCRIPT: Python source. COMMAND: an argv-encoded command line.
    #: PROJECT: a closed-set project operation name (``sync``/``add``/``lock``).
    source: str
    timeout_seconds: int
    #: Workspace-relative working directory. Must resolve inside the workspace.
    workdir: str = "."
    #: Non-secret environment for the execution itself.
    env: Mapping[str, str] = field(default_factory=dict)
    #: Byte ceiling on each captured stream, enforced by the adapter so a
    #: runaway ``print`` loop cannot exhaust the host's memory.
    max_output_bytes: int = 1_048_576


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of running one request.

    ``exit_code`` is ``None`` only when no process ran (a timeout that killed it,
    or a provider error), which is deliberately different from ``0``.
    """

    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    memory_peak_bytes: int = 0
    #: Set when the *provider* failed, as opposed to the program exiting non-zero.
    error: str | None = None


@dataclass(frozen=True)
class ProviderFileEntry:
    """One entry in a workspace directory listing."""

    path: str
    name: str
    is_dir: bool
    size_bytes: int = 0


class RuntimeProviderPort(Protocol):
    """Operations a compute provider must offer for a workspace runtime.

    Every method is ``async``: a real provider is talking to a daemon or an API
    over a socket, and a synchronous signature here would push that blocking
    call onto the event loop.
    """

    @property
    def name(self) -> str:
        """Short provider identifier, persisted on the runtime row."""
        ...

    # -- durable workspace state -------------------------------------------

    async def ensure_workspace_state(self, workspace_id: str) -> str:
        """Create the durable state for a workspace if it does not exist.

        Returns an opaque reference (a volume name). Idempotent: called on every
        start, and must not destroy or reset existing state.
        """
        ...

    async def delete_workspace_state(self, workspace_id: str) -> None:
        """Remove the workspace's durable state.

        Only called for an explicit workspace deletion, never for a stop. Must
        tolerate the state being absent already.
        """
        ...

    # -- runtime lifecycle --------------------------------------------------

    async def create_runtime(self, spec: RuntimeSpec) -> ProviderRuntimeStatus:
        """Allocate a runtime for the workspace and start it."""
        ...

    async def start_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        """Start an existing, stopped runtime."""
        ...

    async def stop_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        """Stop a running runtime, preserving its durable state."""
        ...

    async def inspect_runtime(self, provider_ref: str) -> ProviderRuntimeStatus | None:
        """Current status, or ``None`` when the runtime no longer exists."""
        ...

    async def delete_runtime(self, provider_ref: str) -> None:
        """Destroy the runtime and its disposable resources (not its volume)."""
        ...

    # -- execution ----------------------------------------------------------

    async def execute(
        self, provider_ref: str, request: ExecutionRequest
    ) -> ExecutionOutcome:
        """Run one bounded request and capture its output.

        Must always return an ``ExecutionOutcome``; a non-zero exit is a result,
        not an exception.
        """
        ...

    # -- files --------------------------------------------------------------

    async def write_file(
        self, provider_ref: str, relative_path: str, data: bytes
    ) -> None:
        """Write ``data`` to a workspace-relative path, creating parents.

        Raises the provider error type for a path that escapes the workspace.
        """
        ...

    async def read_file(self, provider_ref: str, relative_path: str) -> bytes:
        """Read a workspace-relative file."""
        ...

    async def list_files(
        self, provider_ref: str, relative_path: str = "."
    ) -> list[ProviderFileEntry]:
        """List one workspace directory (non-recursive)."""
        ...

    async def delete_file(self, provider_ref: str, relative_path: str) -> None:
        """Delete a workspace-relative file or directory."""
        ...

    async def measure_workspace_usage(self, provider_ref: str) -> int:
        """Bytes the workspace's durable files occupy.

        Used to enforce a per-workspace disk budget. Returns ``0`` when the
        usage cannot be measured (a stopped runtime), which is the safe
        direction: an unmeasurable workspace must not be treated as full.
        """
        ...

    # -- snapshots ----------------------------------------------------------

    async def create_snapshot(
        self, provider_ref: str, *, workspace_id: str, revision_id: str
    ) -> str:
        """Save the runtime's environment as a reusable snapshot.

        The adapter mints the snapshot's own name from the workspace and revision
        ids it is given — it is not told a name, because a provider-shaped name
        chosen anywhere above this boundary would be a leak of provider detail
        into the application layer.

        Returns the handle to store on the revision row and to pass back as
        ``RuntimeSpec.image`` when a later runtime starts from it.
        """
        ...

    # -- diagnostics --------------------------------------------------------

    async def describe(self) -> Mapping[str, Any]:
        """Provider identity and capability information for diagnostics.

        Used by ``/health``-style output and by the configuration check, not by
        any business logic.
        """
        ...
