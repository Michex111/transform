"""One submitted unit of work.

An execution is durable. It is created before any provider call, its status is
updated by the worker that runs it, and it remains readable after the runtime it
ran on has been destroyed and after the browser that started it has closed. That
property is what makes "navigate away and come back" work without the original
UI session, and it is why the API never executes anything itself.

Output handling is deliberately two-tier: a bounded excerpt is stored inline on
the row so a small program's output renders with no second request, and the full
capture is uploaded to object storage and referenced by key. An unbounded inline
column would make one ``print`` loop in a user's script a database and API
problem.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.workspaces.exceptions import InvalidExecutionTransition
from src.domain.workspaces.value_object.execution_mode import ExecutionMode
from src.domain.workspaces.value_object.execution_status import (
    ExecutionStatus,
    can_transition,
    is_terminal,
)

#: Ceiling on the submitted program text. Generous for a script (200 kB is
#: roughly 5 000 lines) while still bounding what one request can make the API
#: hold, and bounded before the body is read by the request-body middleware.
MAX_SOURCE_CHARS = 200_000

#: Characters of stdout/stderr stored inline on the row. The tail is kept rather
#: than the head: the part a user needs is almost always the end of the output
#: (a traceback, a final result), and a head-only excerpt of a failing program
#: tends to show nothing but startup noise.
EXCERPT_LIMIT_CHARS = 8_000


def bound_output(text: str, *, limit: int = EXCERPT_LIMIT_CHARS) -> tuple[str, bool]:
    """Return ``(excerpt, truncated)`` for one captured stream.

    Keeps the tail and prepends a notice naming how much was dropped, so a
    reader cannot mistake a truncated excerpt for the whole output.

    The notice counts against ``limit``, and degrades to a bare ellipsis when the
    budget is too small to carry the full sentence. Without that fallback a small
    limit would produce an excerpt consisting entirely of the notice — the
    output the caller asked for, replaced by an explanation of its absence.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return text, False
    marker = f"… [truncated; {len(text) - limit} earlier characters omitted]\n"
    if len(marker) >= limit:
        marker = "…\n"
    keep = limit - len(marker)
    return marker + text[len(text) - keep :], True


@dataclass(frozen=True)
class ExecutionArtifact:
    """A file the execution produced inside the workspace.

    ``path`` is workspace-relative; ``object_key`` is only set once the API has
    copied the file out of the runtime into object storage, which is what makes
    an artifact reachable after the runtime is gone.
    """

    path: str
    size_bytes: int = 0
    object_key: str | None = None
    content_type: str = "application/octet-stream"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "object_key": self.object_key,
            "content_type": self.content_type,
        }


@dataclass
class Execution:
    execution_id: str
    workspace_id: str
    user_id: int
    mode: ExecutionMode
    source: str
    status: ExecutionStatus = ExecutionStatus.QUEUED
    #: The runtime this execution ran on. ``None`` until a worker claims it, and
    #: kept afterwards as a record of where it ran.
    runtime_id: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    #: The process exit code, when the provider reported one. ``None`` for an
    #: execution that never reached a process (cancelled while queued) — distinct
    #: from ``0``, which means "ran and succeeded".
    exit_code: int | None = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    artifacts: list[ExecutionArtifact] = field(default_factory=list)
    duration_ms: int = 0
    memory_peak_bytes: int = 0
    #: Client-supplied key that makes a retried submit idempotent. ``None`` when
    #: the client did not supply one, in which case every submit is a new
    #: execution — a deliberate default, because silently collapsing two
    #: identical programs into one would be a worse surprise than a duplicate.
    idempotency_key: str | None = None
    #: How the request authenticated, reusing the conversion domain's notion of
    #: origin so an agent-submitted execution is distinguishable from a browser
    #: one without inventing a second enum.
    origin: JobOrigin = JobOrigin.WEB

    def __post_init__(self) -> None:
        source = str(self.source)
        if not source.strip():
            raise InvalidExecutionTransition("An execution requires a non-empty source.")
        if len(source) > MAX_SOURCE_CHARS:
            raise InvalidExecutionTransition(
                f"Execution source exceeds the {MAX_SOURCE_CHARS}-character limit."
            )
        self.source = source

    # -- transitions -------------------------------------------------------

    def _transition(self, target: ExecutionStatus) -> None:
        if not can_transition(self.status, target):
            raise InvalidExecutionTransition(
                f"Cannot move execution {self.execution_id} from {self.status} to {target}."
            )
        self.status = target

    def start(self, *, runtime_id: str, at: datetime | None = None) -> None:
        """A worker claimed the execution and is starting it on a runtime."""
        self._transition(ExecutionStatus.RUNNING)
        self.runtime_id = runtime_id
        self.started_at = at or datetime.now(UTC)

    def succeed(
        self,
        *,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        memory_peak_bytes: int = 0,
        artifacts: list[ExecutionArtifact] | None = None,
        stdout_ref: str | None = None,
        stderr_ref: str | None = None,
        at: datetime | None = None,
    ) -> None:
        self._transition(ExecutionStatus.SUCCEEDED)
        self.exit_code = exit_code
        self.duration_ms = max(0, duration_ms)
        self.memory_peak_bytes = max(0, memory_peak_bytes)
        self._record_output(stdout, stderr, stdout_ref=stdout_ref, stderr_ref=stderr_ref)
        if artifacts:
            self.artifacts = list(artifacts)
        self._finish(at)

    def fail(
        self,
        *,
        error: str,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        stdout_ref: str | None = None,
        stderr_ref: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Mark the execution failed.

        Reachable from ``QUEUED`` too, so an infrastructure fault that prevents
        the work from ever starting is recorded as a failure rather than an
        execution that queues forever.
        """
        self._transition(ExecutionStatus.FAILED)
        self.error = error
        self.exit_code = exit_code
        self.duration_ms = max(0, duration_ms)
        self._record_output(stdout, stderr, stdout_ref=stdout_ref, stderr_ref=stderr_ref)
        self._finish(at)

    def cancel(self, *, at: datetime | None = None) -> bool:
        """Cancel the execution. Returns whether it actually changed state.

        Idempotent on purpose: cancelling a finished execution is a no-op that
        returns ``False`` rather than an error, so a client that races a
        completion does not get a confusing 409 for asking.
        """
        if self.is_terminal():
            return False
        self._transition(ExecutionStatus.CANCELLED)
        self.error = self.error or "Execution cancelled."
        self._finish(at)
        return True

    def time_out(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        duration_ms: int = 0,
        stdout_ref: str | None = None,
        stderr_ref: str | None = None,
        at: datetime | None = None,
    ) -> None:
        self._transition(ExecutionStatus.TIMED_OUT)
        self.error = "Execution exceeded the configured time limit."
        self.duration_ms = max(0, duration_ms)
        self._record_output(stdout, stderr, stdout_ref=stdout_ref, stderr_ref=stderr_ref)
        self._finish(at)

    def _record_output(
        self,
        stdout: str,
        stderr: str,
        *,
        stdout_ref: str | None,
        stderr_ref: str | None,
    ) -> None:
        self.stdout_excerpt, self.stdout_truncated = bound_output(stdout)
        self.stderr_excerpt, self.stderr_truncated = bound_output(stderr)
        self.stdout_ref = stdout_ref
        self.stderr_ref = stderr_ref

    def _finish(self, at: datetime | None) -> None:
        self.completed_at = at or datetime.now(UTC)

    # -- queries -----------------------------------------------------------

    def is_terminal(self) -> bool:
        return is_terminal(self.status)

    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0
