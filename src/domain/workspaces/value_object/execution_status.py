"""The lifecycle of a single execution.

A separate machine from ``JobStatus`` because an execution can be cancelled and
can time out, neither of which a conversion job supports. Keeping them apart
also means adding ``TIMED_OUT`` here cannot accidentally make a conversion job
cancellable by a code path that only knows about the queue.
"""

from enum import StrEnum


class ExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


ALLOWED_EXECUTION_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.QUEUED: frozenset(
        {
            ExecutionStatus.RUNNING,
            # A queued execution can be cancelled by the user before a worker
            # ever claims it, and an infrastructure fault (a dead queue) is a
            # failure rather than an execution that queues forever.
            ExecutionStatus.CANCELLED,
            ExecutionStatus.FAILED,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
    # Terminal states. ``cancel`` on an already-terminal execution must be a
    # no-op rather than an error, so the API stays idempotent — see
    # ``Execution.cancel``.
    ExecutionStatus.SUCCEEDED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.TIMED_OUT: frozenset(),
}

TERMINAL_EXECUTION_STATUSES: frozenset[ExecutionStatus] = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)


def can_transition(current: ExecutionStatus, target: ExecutionStatus) -> bool:
    return target in ALLOWED_EXECUTION_TRANSITIONS.get(current, frozenset())


def is_terminal(status: ExecutionStatus) -> bool:
    return status in TERMINAL_EXECUTION_STATUSES
