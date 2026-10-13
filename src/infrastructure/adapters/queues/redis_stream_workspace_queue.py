"""Redis Streams producer for workspace work (executions and snapshots).

Follows the conversion queue's shape deliberately — namespaced stream names,
``maxlen`` bounded for the dead-letter stream, values stringified because Redis
rejects ``None`` — so there is one queue idiom in the codebase rather than two.

The one departure is that the message is a **pointer, not a payload**: it carries
the id of a row that is already committed. The queue is a wake-up, and losing a
message costs a retry, never the record of what was asked for.
"""

from dataclasses import dataclass

from redis.asyncio import Redis

from src.domain.workspaces.entities.execution import Execution
from src.domain.workspaces.entities.runtime_image_revision import RuntimeImageRevision
from src.infrastructure.adapters.queues.stream_names import (
    WORKSPACE_WORK_DEAD_LETTER_STREAM,
    WORKSPACE_WORK_STREAM,
    qualify,
)

#: Wake-up kinds carried on the shared workspace stream.
WORK_KIND_EXECUTION = "execution"
WORK_KIND_SNAPSHOT = "snapshot"

_DEAD_LETTER_MAXLEN = 10_000


@dataclass
class WorkspaceWorkMessage:
    """One unit of workspace work, as it travels over the stream."""

    kind: str
    workspace_id: str
    execution_id: str | None = None
    revision_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Stringified, dropping absent ids.

        ``xadd`` rejects ``None``, which is why the conversion message does the
        same thing — a missing field must be omitted, not sent as an empty value
        that a consumer would have to distinguish from a real empty id.
        """
        payload = {"kind": self.kind, "workspace_id": self.workspace_id}
        if self.execution_id is not None:
            payload["execution_id"] = self.execution_id
        if self.revision_id is not None:
            payload["revision_id"] = self.revision_id
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "WorkspaceWorkMessage":
        return cls(
            kind=str(raw.get("kind", "")),
            workspace_id=str(raw.get("workspace_id", "")),
            execution_id=(
                str(raw["execution_id"]) if raw.get("execution_id") else None
            ),
            revision_id=(str(raw["revision_id"]) if raw.get("revision_id") else None),
        )


class WorkspaceWorkStream:
    """Publishes workspace work onto the namespaced stream."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self.stream_name = qualify(WORKSPACE_WORK_STREAM)

    async def publish_execution(self, execution: Execution) -> None:
        await self._publish(
            WorkspaceWorkMessage(
                kind=WORK_KIND_EXECUTION,
                workspace_id=execution.workspace_id,
                execution_id=execution.execution_id,
            )
        )

    async def publish_snapshot(self, revision: RuntimeImageRevision) -> None:
        await self._publish(
            WorkspaceWorkMessage(
                kind=WORK_KIND_SNAPSHOT,
                workspace_id=revision.workspace_id,
                revision_id=revision.revision_id,
            )
        )

    async def _publish(self, message: WorkspaceWorkMessage) -> None:
        # ``dict`` unparameterised, matching the conversion queue: the redis
        # stubs resolve ``xadd``'s field type from the argument, and an explicit
        # ``dict[str, str]`` does not satisfy the overload they declare.
        payload: dict = message.to_dict()
        await self._redis.xadd(self.stream_name, payload)

    async def dead_letter(self, message_id: str, error: str) -> None:
        """Move an unprocessable entry to the bounded dead-letter stream.

        Bounded on purpose: an unbounded dead letter is a slow memory leak that
        only becomes visible when the instance dies.
        """
        await self._redis.xadd(
            qualify(WORKSPACE_WORK_DEAD_LETTER_STREAM),
            {"original_message_id": message_id, "error": error[:900]},
            maxlen=_DEAD_LETTER_MAXLEN,
            approximate=True,
        )
