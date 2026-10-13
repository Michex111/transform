"""The disposable compute instance attached to a workspace.

Its identity (``runtime_id``) is internal. The user-facing identity of the thing
they opened is the *workspace* id; a runtime id changes every time the runtime is
replaced, which is precisely why it must never appear in a URL or a client
contract.

Provider-specific state is confined to two fields — ``provider`` (a short name
like ``"docker"``) and ``provider_ref`` (an opaque handle such as a container id).
Nothing outside the adapter is allowed to interpret ``provider_ref``; the domain
only stores and returns it.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.domain.workspaces.exceptions import InvalidRuntimeTransition
from src.domain.workspaces.value_object.resource_profile import DEFAULT_RESOURCE_PROFILE
from src.domain.workspaces.value_object.runtime_state import (
    RuntimeState,
    can_transition,
    is_serving,
)


@dataclass
class WorkspaceRuntime:
    """One runtime instance for one workspace.

    There is at most one per workspace, enforced by the database; this entity
    represents whichever one currently exists, not a history of them.
    """

    runtime_id: str
    workspace_id: str
    provider: str
    state: RuntimeState = RuntimeState.STOPPED
    #: Handle into the provider (a container id / sandbox id). Opaque here.
    provider_ref: str | None = None
    #: The runtime image revision this instance was started from, when it was
    #: restored from a snapshot rather than created fresh from the base image.
    image_revision_id: str | None = None
    resource_profile: str = DEFAULT_RESOURCE_PROFILE
    failure_reason: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_activity_at: datetime | None = None
    #: Provider-reported extras (image digest, actual memory limit, …). Read-only
    #: as far as the domain is concerned; surfaced for diagnostics only.
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    # -- transitions -------------------------------------------------------

    def _transition(self, target: RuntimeState) -> None:
        if not can_transition(self.state, target):
            raise InvalidRuntimeTransition(
                f"Cannot move runtime {self.runtime_id} from {self.state} to {target}."
            )
        self.state = target

    def begin_start(self) -> None:
        """Claim the start transition.

        Only legal from ``STOPPED`` or ``FAILED``. An already-``RUNNING`` runtime
        raises instead of being "started" again — the service is expected to
        answer such a request with the current state, and doing that check in one
        place is what prevents a second container from being allocated.
        """
        self._transition(RuntimeState.STARTING)
        self.failure_reason = None

    def mark_running(self, *, provider_ref: str, image_revision_id: str | None = None) -> None:
        """The provider confirmed the runtime is up and usable."""
        self._transition(RuntimeState.RUNNING)
        self.provider_ref = provider_ref
        self.image_revision_id = image_revision_id
        now = datetime.now(UTC)
        self.started_at = now
        self.last_activity_at = now
        self.failure_reason = None

    def begin_stop(self) -> None:
        """Ask the provider to stop the runtime.

        Legal from ``RUNNING`` and from ``STARTING`` — abandoning a slow start is
        a legitimate user action and must not require waiting for a container the
        user has already decided they do not want.
        """
        self._transition(RuntimeState.STOPPING)

    def mark_stopped(self) -> None:
        """The provider confirmed the runtime is no longer running.

        ``provider_ref`` is deliberately **kept**. A stopped container still
exists, and that is the whole reason an ordinary stop/start preserves the
uv-managed environment: the next start restarts the same container rather
than allocating a new one and reinstalling everything. A handle that has
genuinely gone away is detected by the provider (``inspect_runtime`` returns
``None``), which is a better test than guessing here — and the alternative,
clearing it on every stop, silently turned every restart into a recreate.

        Also reachable from ``FAILED``: a runtime whose provider resources have
        been cleaned up is stopped, and saying so is more truthful than leaving a
        failure recorded forever.
        """
        self._transition(RuntimeState.STOPPED)
        self.stopped_at = datetime.now(UTC)

    def mark_failed(self, reason: str) -> None:
        """Record a provider or startup failure.

        Reaching ``FAILED`` from any non-terminal state is allowed by the
        transition table, because the failure can happen during a start, while
        serving, or during a stop.
        """
        if self.state is RuntimeState.FAILED:
            # Idempotent: a second failure report must not raise, or a retry
            # loop that reports the same provider error would fail the request.
            self.failure_reason = reason
            return
        self._transition(RuntimeState.FAILED)
        self.failure_reason = reason

    def touch(self, at: datetime | None = None) -> None:
        self.last_activity_at = at or datetime.now(UTC)

    # -- queries -----------------------------------------------------------

    def is_serving(self) -> bool:
        """True only while the runtime can accept an execution."""
        return is_serving(self.state)

    def needs_provider_start(self) -> bool:
        """True when an execution must start the runtime first."""
        return self.state in {RuntimeState.STOPPED, RuntimeState.FAILED}
