"""Saved workflows: creation, management, and running them.

The service owns three rules the rest of the system relies on:

1. **A definition is validated before it is stored.** Everything downstream may
   then treat a stored definition as well-formed.
2. **A run re-authorizes every file.** A workflow never carries a permission.
   Files are resolved and ownership-checked at run time, through the same path a
   manual batch takes, so a saved workflow cannot outlive or widen the access the
   user had when they saved it.
3. **A run is a batch.** There is no separate execution engine: a run resolves
   its files and delegates to the batch path with the workflow's id attached, so
   progress, retry, download and history are identical to a manual batch.

The service deliberately knows nothing about ORM models or where an extension
comes from. It works on :class:`WorkflowRunFile`, which the presentation layer
builds from a file record — that keeps this module free of imports from
``infrastructure`` and leaves it testable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import uuid4

from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.policies.conversion_policy import is_supported
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.workflows.entities.saved_workflow import (
    InvalidWorkflowError,
    SavedWorkflow,
    parse_definition,
    validate_description,
    validate_name,
)


class WorkflowNotFoundError(Exception):
    """The workflow does not exist, or is not the caller's."""


@dataclass(frozen=True)
class WorkflowRunFile:
    """One selected file, resolved by the caller into what a run needs.

    ``source_format`` is resolved once by the presentation layer (which already
    does that for the single-conversion and batch endpoints) so this service
    never has to know how a file name maps to a format.
    """

    file_id: str
    file_name: str
    object_key: str
    source_format: str


@dataclass(frozen=True)
class RunFileProblem:
    """A selected file this run will not convert, and why."""

    file_id: str
    error: str
    file_name: str | None = None


class WorkflowService:
    def __init__(self, repository, *, max_batch_files: int):
        # Typed loosely: the port is a structural Protocol, and importing it
        # here would buy nothing at runtime while adding a dependency.
        self._repository = repository
        self._max_batch_files = max_batch_files

    # ---- CRUD ----

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        description: str | None,
        definition: dict,
    ) -> SavedWorkflow:
        """Validate and store a new workflow."""
        workflow = SavedWorkflow(
            workflow_id=str(uuid4()),
            user_id=user_id,
            name=validate_name(name),
            description=validate_description(description),
            definition=parse_definition(definition),
        )
        await self._repository.create_workflow(workflow)
        return workflow

    async def list_workflows(self, user_id: int) -> List[SavedWorkflow]:
        """The user's workflows, newest first.

        Named ``list_workflows`` rather than ``list`` on purpose: a method
        called ``list`` shadows the builtin inside the class body, which makes
        every later ``list[...]`` annotation in this class resolve to the
        method instead of the type.
        """
        return await self._repository.list_workflows(user_id)

    async def get(self, workflow_id: str, user_id: int) -> SavedWorkflow:
        """Fetch one workflow or raise :class:`WorkflowNotFoundError`.

        Missing and not-owned raise the same error on purpose: distinguishing
        them would confirm the existence of another account's workflow to anyone
        who guessed an id.
        """
        workflow = await self._repository.get_workflow(workflow_id, user_id)
        if workflow is None:
            raise WorkflowNotFoundError()
        return workflow

    async def update(
        self,
        *,
        workflow_id: str,
        user_id: int,
        name: str,
        description: str | None,
        definition: dict,
    ) -> SavedWorkflow:
        """Replace a workflow's name, description and definition."""
        existing = await self.get(workflow_id, user_id)
        existing.name = validate_name(name)
        existing.description = validate_description(description)
        existing.definition = parse_definition(definition)
        updated = await self._repository.update_workflow(existing)
        if not updated:
            # The row vanished between the read and the write (a concurrent
            # delete). Reported as missing rather than as success.
            raise WorkflowNotFoundError()
        return existing

    async def delete(self, workflow_id: str, user_id: int) -> None:
        """Delete a workflow.

        A delete never touches the conversions the workflow produced. Those
        belong to the user's own history — the work really happened — so the
        jobs keep their ``workflow_id`` as a dangling reference rather than
        being cascaded away.
        """
        deleted = await self._repository.delete_workflow(workflow_id, user_id)
        if not deleted:
            raise WorkflowNotFoundError()

    # ---- Running ----

    def enforce_batch_limit(self, file_count: int) -> None:
        """Raise when a run selects more files than one batch may hold."""
        if file_count > self._max_batch_files:
            raise InvalidWorkflowError(
                f"A workflow run may convert at most {self._max_batch_files} files at a time."
            )

    def validate_against_files(
        self,
        workflow: SavedWorkflow,
        files: list[WorkflowRunFile],
        *,
        supported,
    ) -> list[RunFileProblem]:
        """Report which selected files this workflow cannot convert.

        Checked *before* the run starts, so the user learns that one of their
        files is unsupported while looking at the confirmation rather than one
        failure at a time afterwards. ``supported`` is the registry's conversion
        list, passed in so this stays a pure function of its inputs.
        """
        targets = workflow.definition.target_formats()
        problems: list[RunFileProblem] = []

        for entry in files:
            if not entry.source_format:
                problems.append(
                    RunFileProblem(
                        file_id=entry.file_id,
                        file_name=entry.file_name,
                        error="Could not tell what format this file is.",
                    )
                )
                continue
            # Every operation must be possible for the file, because the run
            # performs all of them; reporting only the first failure would hide
            # a problem the user is about to hit.
            for target in targets:
                conversion = ConversionType(
                    source_format=entry.source_format, target_format=target
                )
                # `is_supported` RAISES rather than returning a bool, so this has
                # to be a try/except. Calling it as `if not is_supported(...)`
                # inverts the result: the success path returns `None`, which is
                # falsy, so every eligible file was reported as unsupported.
                try:
                    is_supported(conversion, supported)
                except InvalidConversion:
                    problems.append(
                        RunFileProblem(
                            file_id=entry.file_id,
                            file_name=entry.file_name,
                            error=(
                                f"{entry.source_format.upper()} cannot be converted "
                                f"to {target.upper()}."
                            ),
                        )
                    )
                    break
        return problems
