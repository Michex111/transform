"""The durable workspace.

This is the aggregate the user actually owns, and the reason the feature exists:
it is the thing that must survive a stopped runtime, a replaced container, a
provider outage and an application deploy. Its identity is
``workspace_id`` — a stable, server-minted id that is never a container name, a
volume name or a provider identifier. Everything provider-specific lives on
``WorkspaceRuntime`` (see that module).

The validation gate is ``__post_init__`` rather than a separate factory, so an
invalid workspace cannot be constructed at all — including by a repository
rehydrating a corrupted row, which is exactly when silently accepting bad data
is most expensive.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.domain.workspaces.exceptions import InvalidWorkspaceError
from src.domain.workspaces.value_object.runtime_config import RuntimeConfig
from src.domain.workspaces.value_object.workspace_state import (
    WorkspaceState,
    accepts_executions,
    can_transition,
)

#: Bounds on user-supplied text. Enforced here and mirrored by the request
#: schema, so a validation error is a 422 from the API rather than a row that
#: cannot be rendered.
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 400


def validate_name(raw: str | None) -> str:
    """Normalise and validate a workspace name.

    Whitespace is collapsed rather than merely trimmed: a name of three spaces
    would otherwise pass a non-empty check and render as a blank row in the list.
    """
    if raw is None:
        raise InvalidWorkspaceError("A workspace name is required.")
    name = " ".join(str(raw).split())
    if not name:
        raise InvalidWorkspaceError("A workspace name is required.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidWorkspaceError(
            f"A workspace name may be at most {MAX_NAME_LENGTH} characters."
        )
    return name


def validate_description(raw: str | None) -> str:
    if raw is None:
        return ""
    description = str(raw).strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidWorkspaceError(
            f"A workspace description may be at most {MAX_DESCRIPTION_LENGTH} characters."
        )
    return description


@dataclass
class Workspace:
    """A durable, user-owned Python workspace."""

    workspace_id: str
    user_id: int
    name: str
    runtime_config: RuntimeConfig = field(default_factory=RuntimeConfig)
    description: str = ""
    state: WorkspaceState = WorkspaceState.ACTIVE
    #: Set by the database on insert/update. ``None`` for a workspace that has
    #: not been persisted yet, which is also how a caller can tell the two apart.
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: When the workspace last did something a user would recognise as activity
    #: (an execution submitted, a runtime started). Distinct from ``updated_at``,
    #: which also moves on a rename or a description edit — the list view wants
    #: "last used", not "last edited".
    last_activity_at: datetime | None = None
    #: Usage metadata. ``execution_count`` is maintained by the repository on
    #: submit so the list view has a number without an aggregate query per row.
    execution_count: int = 0

    def __post_init__(self) -> None:
        self.name = validate_name(self.name)
        self.description = validate_description(self.description)
        if self.execution_count < 0:
            raise InvalidWorkspaceError("execution_count cannot be negative.")

    # -- state transitions -------------------------------------------------

    def _transition(self, target: WorkspaceState) -> None:
        if not can_transition(self.state, target):
            raise InvalidWorkspaceError(
                f"Cannot move a workspace from {self.state} to {target}."
            )
        self.state = target

    def archive(self) -> None:
        """Stop accepting executions, keep every file."""
        self._transition(WorkspaceState.ARCHIVED)

    def reactivate(self) -> None:
        self._transition(WorkspaceState.ACTIVE)

    def begin_delete(self) -> None:
        """Mark the workspace as being torn down.

        Deliberately not the delete itself: the row is removed only after the
        provider resources are gone, so a provider failure leaves a visible,
        retryable workspace instead of a volume nobody has a record of.
        """
        self._transition(WorkspaceState.DELETING)

    def abort_delete(self) -> None:
        """Put a workspace whose teardown failed back into ``ARCHIVED``."""
        self._transition(WorkspaceState.ARCHIVED)

    def accepts_executions(self) -> bool:
        return accepts_executions(self.state)

    # -- edits -------------------------------------------------------------

    def rename(self, name: str) -> None:
        self.name = validate_name(name)

    def update_description(self, description: str | None) -> None:
        self.description = validate_description(description)

    def update_runtime_config(self, config: RuntimeConfig) -> None:
        """Replace the runtime configuration.

        Allowed only while no runtime is attached; the *service* enforces that,
        because the workspace has no visibility into the runtime's existence.
        """
        self.runtime_config = config

    def touch(self, at: datetime | None = None) -> None:
        """Record user-visible activity."""
        self.last_activity_at = at or datetime.now(UTC)
