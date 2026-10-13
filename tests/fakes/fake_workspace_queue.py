"""A recording :class:`WorkspaceQueuePort` for tests.

Records what was published, and can be told to fail, so the "the durable row
exists but the signal never went out" path is exercised rather than assumed.
"""

from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision


class FakeWorkspaceQueue:
    def __init__(self) -> None:
        self.executions: list[Execution] = []
        self.snapshots: list[RuntimeImageRevision] = []
        #: Raise this from the next matching publish call.
        self.fail_executions: Exception | None = None
        self.fail_snapshots: Exception | None = None

    async def publish_execution(self, execution: Execution) -> None:
        if self.fail_executions is not None:
            raise self.fail_executions
        self.executions.append(execution)

    async def publish_snapshot(self, revision: RuntimeImageRevision) -> None:
        if self.fail_snapshots is not None:
            raise self.fail_snapshots
        self.snapshots.append(revision)
