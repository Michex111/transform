"""An in-memory :class:`RuntimeProviderPort` for tests.

It models the provider's *observable* behaviour rather than its implementation:
a runtime can be created, started, stopped, inspected and deleted; files live in
a per-workspace dictionary; snapshots are remembered. Failures are injectable, so
the "provider is unavailable" paths can be exercised without a daemon.

Two things it deliberately reproduces because they are load-bearing:

* **A stopped runtime keeps its files.** That is what makes "stop, restart,
  continue" testable at all.
* **Deleting a runtime does not delete workspace state.** Confusing the two is
  the mistake that would destroy user data, so the fake must not model them as
  the same operation.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.application.exceptions.runtime_provider_exceptions import (
    ProviderExecutionError,
    RuntimeNotFoundError,
    RuntimeProviderError,
    RuntimeProviderUnavailableError,
)
from src.application.ports.runtime_provider_port import (
    ExecutionOutcome,
    ExecutionRequest,
    ProviderFileEntry,
    ProviderRuntimeStatus,
    RuntimeSpec,
)
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.runtime_state import RuntimeState


@dataclass
class FakeRuntime:
    provider_ref: str
    workspace_id: str
    image: str
    state: RuntimeState = RuntimeState.RUNNING
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeRuntimeProvider:
    """A provider that keeps runtime state in memory."""

    def __init__(self, name: str = "fake") -> None:
        self._name = name
        self.volumes: set[str] = set()
        self.runtimes: dict[str, FakeRuntime] = {}
        #: ``workspace_id -> {relative_path: bytes}``. Survives container (runtime)
        #: deletion, exactly like a real volume.
        self.workspace_files: dict[str, dict[str, bytes]] = {}
        self.snapshots: dict[str, str] = {}
        self.projects: dict[str, bytes] = {}
        #: Execution requests the fake was asked to run, in order.
        self.executed: list[tuple[str, ExecutionRequest]] = []
        #: ``provider_ref -> ExecutionOutcome`` scripts, consumed once each.
        self.scripted_outcomes: list[ExecutionOutcome] = []
        #: Raise this from the next call of the named method.
        self.fail_next: dict[str, Exception] = {}
        self.calls: list[str] = []
        self._next_ref = 0

    @property
    def name(self) -> str:
        return self._name

    # -- helpers -----------------------------------------------------------

    def seed_file(self, workspace_id: str, path: str, data: bytes) -> None:
        self.workspace_files.setdefault(workspace_id, {})[path] = data

    def _maybe_fail(self, method: str) -> None:
        self.calls.append(method)
        error = self.fail_next.pop(method, None)
        if error is not None:
            raise error

    def _require(self, provider_ref: str) -> FakeRuntime:
        runtime = self.runtimes.get(provider_ref)
        if runtime is None:
            raise RuntimeNotFoundError("The runtime no longer exists.")
        return runtime

    def _new_ref(self, workspace_id: str) -> str:
        self._next_ref += 1
        return f"{self._name}-{workspace_id}-{self._next_ref}"

    # -- durable state -----------------------------------------------------

    async def ensure_workspace_state(self, workspace_id: str) -> str:
        self._maybe_fail("ensure_workspace_state")
        self.volumes.add(workspace_id)
        self.workspace_files.setdefault(workspace_id, {})
        return f"volume-{workspace_id}"

    async def delete_workspace_state(self, workspace_id: str) -> None:
        self.calls.append("delete_workspace_state")
        self.volumes.discard(workspace_id)
        self.workspace_files.pop(workspace_id, None)

    # -- lifecycle ---------------------------------------------------------

    async def create_runtime(self, spec: RuntimeSpec) -> ProviderRuntimeStatus:
        self._maybe_fail("create_runtime")
        ref = self._new_ref(spec.workspace_id)
        self.runtimes[ref] = FakeRuntime(
            provider_ref=ref, workspace_id=spec.workspace_id, image=spec.image
        )
        return ProviderRuntimeStatus(
            provider_ref=ref,
            state=RuntimeState.RUNNING,
            image=spec.image,
            metadata={"created_from": spec.image},
        )

    async def start_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        self._maybe_fail("start_runtime")
        runtime = self._require(provider_ref)
        runtime.state = RuntimeState.RUNNING
        return ProviderRuntimeStatus(
            provider_ref=provider_ref, state=runtime.state, image=runtime.image
        )

    async def stop_runtime(self, provider_ref: str) -> ProviderRuntimeStatus:
        self._maybe_fail("stop_runtime")
        runtime = self._require(provider_ref)
        runtime.state = RuntimeState.STOPPED
        return ProviderRuntimeStatus(
            provider_ref=provider_ref, state=runtime.state, image=runtime.image
        )

    async def inspect_runtime(self, provider_ref: str) -> ProviderRuntimeStatus | None:
        self.calls.append("inspect_runtime")
        runtime = self.runtimes.get(provider_ref)
        if runtime is None:
            return None
        return ProviderRuntimeStatus(
            provider_ref=provider_ref, state=runtime.state, image=runtime.image
        )

    async def delete_runtime(self, provider_ref: str) -> None:
        self._maybe_fail("delete_runtime")
        self.runtimes.pop(provider_ref, None)

    # -- execution ---------------------------------------------------------

    async def execute(
        self, provider_ref: str, request: ExecutionRequest
    ) -> ExecutionOutcome:
        self._maybe_fail("execute")
        runtime = self._require(provider_ref)
        if runtime.state is not RuntimeState.RUNNING:
            raise ProviderExecutionError("The runtime is not running.")
        self.executed.append((provider_ref, request))
        if self.scripted_outcomes:
            return self.scripted_outcomes.pop(0)
        if request.mode is ExecutionMode.PROJECT:
            return ExecutionOutcome(exit_code=0, stdout="synced\n", duration_ms=5)
        return ExecutionOutcome(exit_code=0, stdout="ok\n", duration_ms=5)

    # -- files -------------------------------------------------------------

    async def write_file(
        self, provider_ref: str, relative_path: str, data: bytes
    ) -> None:
        self._maybe_fail("write_file")
        runtime = self._require(provider_ref)
        self.workspace_files.setdefault(runtime.workspace_id, {})[
            relative_path
        ] = data

    async def read_file(self, provider_ref: str, relative_path: str) -> bytes:
        self.calls.append("read_file")
        runtime = self._require(provider_ref)
        files = self.workspace_files.get(runtime.workspace_id, {})
        if relative_path not in files:
            raise ProviderExecutionError(f"No such file: {relative_path}")
        return files[relative_path]

    async def list_files(
        self, provider_ref: str, relative_path: str = "."
    ) -> list[ProviderFileEntry]:
        self.calls.append("list_files")
        runtime = self._require(provider_ref)
        prefix = "" if relative_path in (".", "") else relative_path.rstrip("/") + "/"
        entries: list[ProviderFileEntry] = []
        for path, data in sorted(
            self.workspace_files.get(runtime.workspace_id, {}).items()
        ):
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix) :]
            if "/" in remainder:
                directory = remainder.split("/")[0]
                # No trailing slash: the real adapter returns the bare entry
                # name, and a fake that disagrees hides a UI bug.
                if all(entry.name != directory for entry in entries):
                    entries.append(
                        ProviderFileEntry(
                            path=f"{prefix}{directory}", name=directory, is_dir=True
                        )
                    )
                continue
            entries.append(
                ProviderFileEntry(path=path, name=remainder, is_dir=False, size_bytes=len(data))
            )
        return entries

    async def delete_file(self, provider_ref: str, relative_path: str) -> None:
        self._maybe_fail("delete_file")
        runtime = self._require(provider_ref)
        files = self.workspace_files.get(runtime.workspace_id, {})
        targets = [
            path
            for path in files
            if path == relative_path or path.startswith(relative_path.rstrip("/") + "/")
        ]
        for path in targets:
            files.pop(path, None)

    async def measure_workspace_usage(self, provider_ref: str) -> int:
        """Total apparent bytes of the workspace's files.

        Models the real ``du -sb``: the sum of the file sizes, which is what the
        quota is expressed in. A stopped runtime reports 0 — the real adapter's
        safe direction, since an unmeasurable workspace must not read as full.
        """
        self.calls.append("measure_workspace_usage")
        runtime = self.runtimes.get(provider_ref)
        if runtime is None or runtime.state is not RuntimeState.RUNNING:
            return 0
        return sum(
            len(data) for data in self.workspace_files.get(runtime.workspace_id, {}).values()
        )

    # -- snapshots ---------------------------------------------------------

    async def create_snapshot(
        self, provider_ref: str, *, workspace_id: str, revision_id: str
    ) -> str:
        self._maybe_fail("create_snapshot")
        self._require(provider_ref)
        ref = f"{self._name}-env:{workspace_id}-{revision_id}"
        self.snapshots[revision_id] = ref
        return ref

    # -- diagnostics -------------------------------------------------------

    async def describe(self) -> Mapping[str, Any]:
        if self.fail_next.pop("describe", None):
            raise RuntimeProviderUnavailableError("The provider is not reachable.")
        return {"provider": self._name, "available": True, "network_disabled": False}


def unavailable(name: str = "fake") -> RuntimeProviderError:
    """A ready-made 'provider is down' failure for :attr:`FakeRuntimeProvider.fail_next`."""
    return RuntimeProviderUnavailableError(f"{name} is unreachable")
