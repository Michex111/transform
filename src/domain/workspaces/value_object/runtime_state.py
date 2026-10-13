"""The lifecycle of a runtime instance.

Deliberately its own state machine rather than a reuse of ``JobStatus``: a
conversion job has a start and an end, while a runtime cycles
(``STOPPED`` → ``RUNNING`` → ``STOPPED``) for as long as the workspace exists.
Sharing one enum would force every consumer to reason about states that cannot
occur for the thing it is looking at.
"""

from enum import StrEnum


class RuntimeState(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


#: The legal transitions. Everything not listed is rejected by
#: ``WorkspaceRuntime`` instead of applied, because a runtime that silently
#: accepts a bogus transition is one the API can no longer describe truthfully.
ALLOWED_RUNTIME_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    RuntimeState.STOPPED: frozenset({RuntimeState.STARTING}),
    RuntimeState.STARTING: frozenset(
        {
            RuntimeState.RUNNING,
            # A start can be abandoned or fail: the provider may reject the
            # allocation, the base image may be missing, or the user may ask to
            # stop before the container has finished coming up.
            RuntimeState.STOPPING,
            RuntimeState.FAILED,
        }
    ),
    RuntimeState.RUNNING: frozenset({RuntimeState.STOPPING, RuntimeState.FAILED}),
    # A stop the provider never confirms (a hung daemon) is a failure, not a
    # runtime parked in ``STOPPING`` forever.
    RuntimeState.STOPPING: frozenset({RuntimeState.STOPPED, RuntimeState.FAILED}),
    # ``FAILED`` is recoverable by design: the workspace and its files are
    # untouched, so the next start *is* the recovery path. ``STOPPED`` is also
    # reachable so a runtime whose provider resources are gone can be retired
    # without pretending it was ever healthy.
    RuntimeState.FAILED: frozenset({RuntimeState.STARTING, RuntimeState.STOPPED}),
}

TERMINAL_RUNTIME_STATES: frozenset[RuntimeState] = frozenset(
    {RuntimeState.STOPPED, RuntimeState.FAILED}
)
"""States from which the runtime is not currently serving executions."""


def can_transition(current: RuntimeState, target: RuntimeState) -> bool:
    """True when ``current → target`` is a legal transition."""
    return target in ALLOWED_RUNTIME_TRANSITIONS.get(current, frozenset())


def is_serving(state: RuntimeState) -> bool:
    """True only while the runtime can actually accept an execution.

    ``True`` for ``RUNNING`` alone: a ``STARTING`` runtime is not yet usable, and
    treating it as usable is how an execution gets submitted to a container that
    does not exist.
    """
    return state is RuntimeState.RUNNING
