"""The lifecycle of the durable workspace itself.

Kept independent of :class:`~src.domain.workspaces.value_object.runtime_state.RuntimeState`
on purpose. A workspace stays ``ACTIVE`` whether or not a runtime is attached —
conflating the two is exactly the mistake the brief warns against, because it
makes "my workspace disappeared" indistinguishable from "my runtime is off".
"""

from enum import StrEnum


class WorkspaceState(StrEnum):
    ACTIVE = "ACTIVE"
    #: Read-only: the workspace and its files are kept, but new executions are
    #: refused and its runtime must be stopped. This is the safe way to retire a
    #: workspace without destroying the user's files.
    ARCHIVED = "ARCHIVED"
    #: Transient: set while the API tears down the runtime, snapshot and volume.
    #: The row is removed only after that succeeds, so a failed teardown leaves
    #: a workspace the user can see and retry rather than an orphaned volume
    #: nobody knows about.
    DELETING = "DELETING"


ALLOWED_WORKSPACE_TRANSITIONS: dict[WorkspaceState, frozenset[WorkspaceState]] = {
    WorkspaceState.ACTIVE: frozenset({WorkspaceState.ARCHIVED, WorkspaceState.DELETING}),
    WorkspaceState.ARCHIVED: frozenset({WorkspaceState.ACTIVE, WorkspaceState.DELETING}),
    # ``DELETING`` is reversible only back to ``ARCHIVED``: a user who retries a
    # failed delete is describing the state they want, not re-activating.
    WorkspaceState.DELETING: frozenset({WorkspaceState.ARCHIVED}),
}

#: States in which a workspace accepts new executions.
USABLE_WORKSPACE_STATES: frozenset[WorkspaceState] = frozenset({WorkspaceState.ACTIVE})


def can_transition(current: WorkspaceState, target: WorkspaceState) -> bool:
    return target in ALLOWED_WORKSPACE_TRANSITIONS.get(current, frozenset())


def accepts_executions(state: WorkspaceState) -> bool:
    return state in USABLE_WORKSPACE_STATES
