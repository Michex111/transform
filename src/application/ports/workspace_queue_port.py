"""The queue port for workspace work.

Two kinds of work are queued, and both are *signals* rather than the source of
truth: an execution row and a runtime-image-revision row are committed to the
database before anything is published. A message that is lost therefore costs a
retry; it can never cost the record of what was asked for.
"""

from typing import Protocol

from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision


class WorkspaceQueuePort(Protocol):
    async def publish_execution(self, execution: Execution) -> None:
        """Signal that an execution is queued and needs a worker."""
        ...

    async def publish_snapshot(self, revision: RuntimeImageRevision) -> None:
        """Signal that an environment snapshot should be built."""
        ...
