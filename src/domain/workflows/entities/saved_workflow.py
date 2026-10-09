"""A saved, reusable workflow definition.

A workflow is a *template*: it names the operations to perform and the target
formats, and the files are chosen when it runs. That choice is deliberate and is
the whole reason this model is small.

The alternative — binding a workflow to specific file ids — creates a record
that rots: the files get deleted, expire, or are moved out of the user's reach,
and every run has to explain why the thing it was saved with no longer exists.
A template cannot rot, because it never held a reference to anything. The spec
asks for exactly this preference ("prefer a reusable template when the operation
is intended to run on newly selected documents"), and it is also the smaller
design.

The operations are an explicit, validated, closed set — never stored free text
and never anything executable. ``definition`` round-trips through JSON, so what
is persisted is data the domain has already agreed to accept; adding a new
operation means extending this module, not teaching the runner to interpret
something new at run time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

#: The operations a workflow may contain. A closed set on purpose: the runner
#: switches on these, and an unknown value is a rejected definition rather than
#: a silently skipped step.
OPERATION_CONVERT = "convert"
SUPPORTED_OPERATIONS: frozenset[str] = frozenset({OPERATION_CONVERT})

#: Bounds that keep a definition storable and a run affordable. They are limits
#: on the *template*, not on the plan: a workflow saves a shape of work, and the
#: per-run authorization and quota checks still happen at run time.
MAX_OPERATIONS = 10
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 400
MAX_FORMAT_LENGTH = 20


class InvalidWorkflowError(ValueError):
    """The definition is not something this system will store or run."""


@dataclass(frozen=True)
class WorkflowOperation:
    """One step: convert the selected files into ``target_format``."""

    type: str
    target_format: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "target_format": self.target_format}


@dataclass(frozen=True)
class WorkflowDefinition:
    """The ordered operations a workflow performs.

    ``source`` names where the files come from. ``"select_at_run"`` is the only
    value the system supports today, and it is stated explicitly rather than
    implied so that a stored definition is self-describing: a future
    ``"folder"`` source can be added without every existing row being
    reinterpreted.
    """

    source: str
    operations: tuple[WorkflowOperation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "operations": [operation.to_dict() for operation in self.operations],
        }

    def target_formats(self) -> tuple[str, ...]:
        """The distinct target formats this workflow will produce.

        Used by the runner to validate every selected file up front, so the user
        learns which files cannot be converted *before* the run starts rather
        than discovering it one failure at a time.
        """
        seen: list[str] = []
        for operation in self.operations:
            if operation.target_format not in seen:
                seen.append(operation.target_format)
        return tuple(seen)


#: The only supported file source. See :class:`WorkflowDefinition`.
SOURCE_SELECT_AT_RUN = "select_at_run"


def parse_definition(raw: Mapping[str, Any] | None) -> WorkflowDefinition:
    """Validate an untrusted definition into a :class:`WorkflowDefinition`.

    Storage and the API both hand us whatever `json` produced, so this is the
    single gate: anything that is not a well-formed definition raises
    :class:`InvalidWorkflowError` here rather than surfacing as an
    ``AttributeError`` deep inside a run.

    Rejecting is strict on purpose. A definition that is *almost* right — a
    misspelled operation, a format string with a leading dot, an empty list —
    would otherwise be stored and then fail at run time, long after the user
    could connect the failure to the thing they saved.
    """
    if not isinstance(raw, Mapping):
        raise InvalidWorkflowError("Workflow definition must be an object.")

    source = raw.get("source", SOURCE_SELECT_AT_RUN)
    if source != SOURCE_SELECT_AT_RUN:
        raise InvalidWorkflowError(
            f"Unsupported workflow source {source!r}; only {SOURCE_SELECT_AT_RUN!r} is supported."
        )

    operations_raw = raw.get("operations")
    if not isinstance(operations_raw, (list, tuple)) or not operations_raw:
        raise InvalidWorkflowError("A workflow must contain at least one operation.")
    if len(operations_raw) > MAX_OPERATIONS:
        raise InvalidWorkflowError(f"A workflow may contain at most {MAX_OPERATIONS} operations.")

    operations: list[WorkflowOperation] = []
    for index, item in enumerate(operations_raw):
        if not isinstance(item, Mapping):
            raise InvalidWorkflowError(f"Operation {index} must be an object.")
        op_type = item.get("type")
        if op_type not in SUPPORTED_OPERATIONS:
            raise InvalidWorkflowError(
                f"Operation {index} has unsupported type {op_type!r}."
            )
        operations.append(
            WorkflowOperation(
                type=str(op_type),
                target_format=normalise_format(item.get("target_format"), index),
            )
        )

    return WorkflowDefinition(source=str(source), operations=tuple(operations))


def normalise_format(value: Any, index: int) -> str:
    """A target format from a definition, normalised and bounded.

    Normalised the same way the conversion endpoint normalises a target
    (lowercased, no leading dot) so a saved workflow and a direct conversion
    cannot disagree about what "PDF" means.
    """
    if not isinstance(value, str):
        raise InvalidWorkflowError(f"Operation {index} is missing a target_format.")
    cleaned = value.strip().lstrip(".").lower()
    if not cleaned:
        raise InvalidWorkflowError(f"Operation {index} has an empty target_format.")
    if len(cleaned) > MAX_FORMAT_LENGTH:
        raise InvalidWorkflowError(
            f"Operation {index} target_format is longer than {MAX_FORMAT_LENGTH} characters."
        )
    return cleaned


def validate_name(name: str | None) -> str:
    """A workflow name, trimmed and bounded."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise InvalidWorkflowError("A workflow name is required.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise InvalidWorkflowError(f"A workflow name may be at most {MAX_NAME_LENGTH} characters.")
    return cleaned


def validate_description(description: str | None) -> str:
    """An optional description, trimmed and bounded."""
    cleaned = (description or "").strip()
    if len(cleaned) > MAX_DESCRIPTION_LENGTH:
        raise InvalidWorkflowError(
            f"A workflow description may be at most {MAX_DESCRIPTION_LENGTH} characters."
        )
    return cleaned


@dataclass
class SavedWorkflow:
    """A user's stored workflow."""

    workflow_id: str
    user_id: int
    name: str
    definition: WorkflowDefinition
    description: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: How many times this workflow has been run. Derived from the jobs that
    #: carry its id, not stored on this row — a counter here would be a second
    #: copy of the truth that a failed run could desynchronise.
    run_count: int = field(default=0)
