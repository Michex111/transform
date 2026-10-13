"""Redis Streams consumer for workspace work.

Deliberately simpler than the conversion-job consumer, and the simplification is
justified rather than careless: this consumes **one** stream, so the per-stream
``COUNT`` semantics that forced the conversion consumer to buffer extra
deliveries do not apply — ``count=1`` over a single stream really does return at
most one entry. There is one consumer group and one PEL.

What it keeps from that consumer, because each one fixed a real message-loss bug:

* the group is created with ``mkstream`` and ``BUSYGROUP`` swallowed, so a
  restart is not an error;
* the dead letter is written **before** the ACK, so a failed dead-letter write
  cannot lose the work;
* stale entries are **re-queued** (re-``xadd``, then ``xack``) rather than merely
  claimed into the PEL and forgotten — claiming was the old no-op recovery.
"""

import asyncio
from dataclasses import dataclass

from redis.asyncio import Redis

from src.infrastructure.adapters.queues.redis_stream_workspace_queue import (
    WorkspaceWorkMessage,
    WorkspaceWorkStream,
)
from src.infrastructure.adapters.queues.stream_names import (
    WORKSPACE_WORK_STREAM,
    qualify,
)
from src.infrastructure.logging.loggers import worker_logger

#: How long a single ``xreadgroup`` blocks before the loop re-checks shutdown.
_BLOCK_MS = 10_000
#: Idle time after which an entry is assumed abandoned by a dead worker. Must
#: exceed the longest execution (the container-side timeout) or a live consumer's
#: in-flight run would be stolen and executed twice.
_MIN_IDLE_MARGIN_MS = 60_000


@dataclass(frozen=True)
class ClaimedWork:
    """One delivery: the stream id plus the parsed message."""

    message_id: str
    message: WorkspaceWorkMessage


class WorkspaceWorkConsumer:
    def __init__(
        self,
        *,
        redis_client: Redis,
        consumer_group: str,
        consumer_name: str,
        execution_timeout_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._group = consumer_group
        self._consumer = consumer_name
        self._stream = qualify(WORKSPACE_WORK_STREAM)
        self._execution_timeout_seconds = execution_timeout_seconds
        self._publisher = WorkspaceWorkStream(redis_client)

    @classmethod
    async def create(
        cls,
        *,
        redis_client: Redis,
        consumer_group: str,
        consumer_name: str,
        execution_timeout_seconds: int,
        max_retries: int = 12,
        base_delay: float = 5.0,
    ) -> "WorkspaceWorkConsumer":
        consumer = cls(
            redis_client=redis_client,
            consumer_group=consumer_group,
            consumer_name=consumer_name,
            execution_timeout_seconds=execution_timeout_seconds,
        )
        delay = base_delay
        for attempt in range(max_retries):
            try:
                await consumer._ensure_group()
                worker_logger.info(
                    "Connected to Redis consumer group %r on %s",
                    consumer_group,
                    consumer.stream_name,
                )
                return consumer
            except Exception as exc:  # noqa: BLE001 - retried below
                if attempt == max_retries - 1:
                    raise
                worker_logger.warning(
                    "Could not create consumer group (attempt %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)
        raise RuntimeError("unreachable: consumer creation loop exhausted")

    @property
    def stream_name(self) -> str:
        return self._stream

    async def _ensure_group(self) -> None:
        try:
            # ``id="0"`` so entries already on the stream when the group is
            # created are still delivered: a message published between a deploy
            # and the group's creation must not be stranded.
            await self._redis.xgroup_create(
                self._stream, self._group, id="0", mkstream=True
            )
        except Exception as exc:  # noqa: BLE001
            if "BUSYGROUP" not in str(exc):
                raise
            worker_logger.debug("Consumer group %r already exists", self._group)

    async def fetch(self) -> ClaimedWork | None:
        response = await self._redis.xreadgroup(  # type: ignore[arg-type]
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=1,
            block=_BLOCK_MS,
        )
        if not response:
            return None
        for _stream_name, messages in response:  # type: ignore[union-attr]
            for message_id, fields in messages:  # type: ignore[union-attr]
                return ClaimedWork(
                    message_id=(
                        message_id.decode()
                        if isinstance(message_id, bytes)
                        else str(message_id)
                    ),
                    message=WorkspaceWorkMessage.from_dict(dict(fields)),  # type: ignore[arg-type]
                )
        return None

    async def acknowledge(self, message_id: str) -> None:
        await self._redis.xack(self._stream, self._group, message_id)

    async def fail(self, message_id: str, error: str) -> None:
        """Dead-letter first, then ACK.

        Order matters and is the whole point: if the dead-letter write fails, the
        entry stays pending and the next stale sweep retries it. ACK-then-DLQ
        would lose the work outright, which is the bug this ordering fixes.
        """
        try:
            await self._publisher.dead_letter(message_id, error)
        except Exception as exc:  # noqa: BLE001 - never skip the ACK because of this
            worker_logger.error("Could not dead-letter %s: %s", message_id, exc)
        try:
            await self.acknowledge(message_id)
        except Exception as exc:  # noqa: BLE001
            worker_logger.error("Could not ACK %s: %s", message_id, exc)

    def stale_min_idle_ms(self) -> int:
        """Idle threshold above which an entry is assumed abandoned.

        Derived from the execution timeout rather than hard-coded: a threshold
        below the timeout would let a peer steal a run that is still executing,
        which means running it twice.
        """
        return self._execution_timeout_seconds * 1000 + _MIN_IDLE_MARGIN_MS

    async def reclaim_stale(self, count: int = 20) -> int:
        """Re-queue entries left pending by a worker that died mid-run."""
        try:
            response = await self._redis.xautoclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self.stale_min_idle_ms(),
                start_id="0-0",
                count=count,
            )
        except Exception as exc:  # noqa: BLE001 - a failed sweep is not fatal
            worker_logger.warning("Stale reclaim failed: %s", exc)
            return 0

        entries = response[1] if isinstance(response, tuple) and len(response) > 1 else []
        requeued = 0
        for entry in entries or []:
            try:
                message_id, fields = entry[0], entry[1]
            except (TypeError, IndexError):  # pragma: no cover - defensive
                continue
            if not fields:
                # An entry Redis reports as deleted: nothing to re-queue, but it
                # still has to leave the PEL or it is reclaimed forever.
                await self._safe_ack(message_id)
                continue
            message = WorkspaceWorkMessage.from_dict(fields)
            try:
                # Re-queue BEFORE acknowledging, so a crash between the two
                # duplicates the work instead of losing it.
                await self._publisher._publish(message)  # noqa: SLF001 - same layer
                await self._safe_ack(message_id)
                requeued += 1
            except Exception as exc:  # noqa: BLE001
                worker_logger.warning("Could not re-queue stale entry: %s", exc)
        return requeued

    async def _safe_ack(self, message_id: str | bytes | int) -> None:
        try:
            await self._redis.xack(self._stream, self._group, message_id)
        except Exception as exc:  # noqa: BLE001
            worker_logger.warning("Could not ACK reclaimed entry: %s", exc)

    async def close(self) -> None:
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()
