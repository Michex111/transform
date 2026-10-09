"""Ports for saved-workflow persistence.

Two protocols, split the same way the conversion ports are: reads and writes
together for the service, and a write-only slice for anything that only creates
rows. Every method is scoped by ``user_id``, and that is the interface's job to
state rather than each caller's to remember — a repository whose reads are not
scoped is one forgotten ``where`` away from showing one account another's
workflows.
"""

from typing import Protocol

from src.domain.workflows.entities.saved_workflow import SavedWorkflow


class SavedWorkflowWriteRepositoryPort(Protocol):
    async def create_workflow(self, workflow: SavedWorkflow) -> None:
        """Insert a workflow."""
        ...

    async def update_workflow(self, workflow: SavedWorkflow) -> bool:
        """Persist a changed name/description/definition.

        Returns False when the row is missing or owned by someone else, so the
        caller can answer 404 rather than pretending the write landed.
        """
        ...

    async def delete_workflow(self, workflow_id: str, user_id: int) -> bool:
        """Delete a workflow. Returns False when it is missing or not owned."""
        ...


class SavedWorkflowRepositoryPort(SavedWorkflowWriteRepositoryPort, Protocol):
    async def get_workflow(self, workflow_id: str, user_id: int) -> SavedWorkflow | None:
        """Fetch one workflow owned by ``user_id``, else None."""
        ...

    async def list_workflows(self, user_id: int) -> list[SavedWorkflow]:
        """The user's workflows, newest first."""
        ...
