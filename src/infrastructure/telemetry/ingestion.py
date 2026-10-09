"""Bounded, non-blocking ingestion for request telemetry.

The problem this solves: an observability feature must not become a new way for
the product to fail. A synchronous INSERT on every API request would add a
database round trip to the hot path and — worse — make the request's success
depend on the telemetry table's availability. A drop-in replacement for the
queue (Redis, Kafka) would be new operational surface for something the existing
infrastructure already handles.

So the write path is an in-process ``asyncio.Queue`` with a hard bound, drained
by one background task that writes batches. Properties that follow from that
design, all deliberate:

* **Recording never blocks and never raises.** ``record_*`` uses
  ``put_nowait``; a full queue drops the item and increments a counter instead
  of awaiting space. Losing telemetry is strictly better than slowing down or
  stalling a user's conversion.
* **Dropping is observable.** ``stats()`` reports depth, drops and write
  failures, and those numbers are published as Prometheus gauges, so "we are
  silently discarding 40% of events" is a metric an operator can alert on rather
  than a surprise discovered during an incident.
* **Failure policy is explicit.** A failed batch is retried **once** on the next
  cycle; if it fails again the batch is dropped and counted. Retrying forever
  against a broken database would turn a bounded drop into an unbounded memory
  leak, which is a worse failure than losing a page of request metadata.
* **Backpressure is the drop.** When the queue is full the producer is never
  slowed; the oldest-arriving items are the ones lost, which keeps the newest
  activity — the activity a user is most likely to be looking at — in the graph.
* **Shutdown drains.** ``stop()`` flushes the remaining buffer so a clean
  deploy does not lose the last few seconds of events.

This is intentionally process-local. Metrics answers are computed from the
database (not from an in-process list), so the loss of one process's queue does
not make another process's dashboard wrong — it only means fewer rows. See the
SSE section of the developer router for why that matters.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from src.domain.telemetry.entities.api_request_event import (
    ApiRequestEvent,
    McpToolInvocation,
)

logger = logging.getLogger(__name__)

#: Sentinel pushed to wake the flusher for shutdown.
_STOP = object()

#: One queued item: a tag and the row to write. A single queue (rather than one
#: per event type) keeps the bound meaningful — a burst of tool calls cannot
#: starve request logging of its share, because they draw on the same budget.
_Item = tuple[str, ApiRequestEvent | McpToolInvocation]


@dataclass
class IngestionStats:
    """Counters describing the health of the write path."""

    enqueued_events: int = 0
    enqueued_invocations: int = 0
    dropped_events: int = 0
    dropped_invocations: int = 0
    written_events: int = 0
    written_invocations: int = 0
    write_failures: int = 0
    retried_batches: int = 0
    queue_depth: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "enqueued_events": self.enqueued_events,
            "enqueued_invocations": self.enqueued_invocations,
            "dropped_events": self.dropped_events,
            "dropped_invocations": self.dropped_invocations,
            "written_events": self.written_events,
            "written_invocations": self.written_invocations,
            "write_failures": self.write_failures,
            "retried_batches": self.retried_batches,
            "queue_depth": self.queue_depth,
        }


@dataclass
class TelemetryIngestion:
    """A bounded in-process queue with a background batch writer.

    ``session_factory`` is any zero-argument callable returning an async context
    manager that yields a SQLAlchemy ``AsyncSession``. It is injected (rather
    than imported) so the flusher is testable against a fake, and so the
    process's real session factory is created lazily on first use.
    """

    session_factory: Callable[[], object] | None = None
    batch_size: int = 100
    max_queue: int = 10_000
    flush_interval_seconds: float = 2.0
    enabled: bool = True

    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, init=False, repr=False)
    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _stats: IngestionStats = field(default_factory=IngestionStats, init=False, repr=False)
    _retry_buffer: list[_Item] = field(default_factory=list, init=False, repr=False)
    _drop_logged: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # ``maxsize`` is fixed at construction so the bound cannot drift; a
        # queue created without one is unbounded, which is the failure mode
        # this whole module exists to avoid.
        self._queue = asyncio.Queue(maxsize=max(1, self.max_queue))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flusher. Idempotent."""
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="telemetry-ingestion")
        logger.info(
            "Telemetry ingestion started (batch=%d, queue=%d, flush=%.1fs)",
            self.batch_size,
            self.max_queue,
            self.flush_interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the flusher, flushing whatever is buffered first."""
        if self._task is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_STOP)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(self._task, timeout=10)
        self._task = None

    async def flush_now(self) -> None:
        """Write every buffered item immediately (used by tests and shutdown)."""
        items = self._drain_available()
        if items or self._retry_buffer:
            await self._write(items)

    # ------------------------------------------------------------------
    # Producer side (never blocks, never raises)
    # ------------------------------------------------------------------

    def record_event(self, event: ApiRequestEvent) -> None:
        self._enqueue(("event", event))

    def record_invocation(self, invocation: McpToolInvocation) -> None:
        self._enqueue(("invocation", invocation))

    def _enqueue(self, item: _Item) -> None:
        tag = item[0]
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # Drop, count, and say so once so the log is not flooded by the same
            # condition on every request while the queue stays saturated.
            if tag == "event":
                self._stats.dropped_events += 1
            else:
                self._stats.dropped_invocations += 1
            if not self._drop_logged:
                self._drop_logged = True
                logger.warning(
                    "Telemetry ingestion queue is full (%d); dropping events. "
                    "The dashboard will under-report until the writer catches up.",
                    self.max_queue,
                )
            return
        self._drop_logged = False
        if tag == "event":
            self._stats.enqueued_events += 1
        else:
            self._stats.enqueued_invocations += 1
        self._stats.queue_depth = self._queue.qsize()

    def stats(self) -> dict[str, int]:
        self._stats.queue_depth = self._queue.qsize()
        return self._stats.as_dict()

    # ------------------------------------------------------------------
    # Consumer side
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                items = await self._collect()
            except asyncio.CancelledError:
                raise
            if items is None:
                # Stop requested: write what we have and exit.
                await self._write(self._drain_available())
                return
            if items:
                await self._write(items)

    async def _collect(self) -> list[_Item] | None:
        """Gather up to ``batch_size`` items, waiting at most ``flush_interval``.

        The first wait is the flush interval, so an idle queue costs one wake-up
        per interval rather than a spin. Returns ``None`` when a stop was
        requested.
        """
        items: list[_Item] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.flush_interval_seconds

        while len(items) < self.batch_size:
            remaining = deadline - loop.time() if items else self.flush_interval_seconds
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(self._queue.get(), remaining)
            except (TimeoutError, asyncio.TimeoutError):
                break
            if item is _STOP:
                # Re-publish so a concurrent second stop is also honoured.
                with contextlib.suppress(asyncio.QueueFull):
                    self._queue.put_nowait(_STOP)
                return None
            items.append(item)  # type: ignore[arg-type]

        return items

    def _drain_available(self) -> list[_Item]:
        items: list[_Item] = []
        while not self._queue.empty() and len(items) < self.batch_size:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _STOP:
                continue
            items.append(item)  # type: ignore[arg-type]
        return items

    async def _write(self, items: list[_Item]) -> None:
        """Persist a batch, retrying once on failure.

        Items already deferred from a previous failure are merged in, capped at
        one batch so the retry buffer itself cannot grow without bound.
        """
        batch = (self._retry_buffer + items)[: self.batch_size]
        self._retry_buffer = []
        if not batch:
            return

        events: list[ApiRequestEvent] = []
        invocations: list[McpToolInvocation] = []
        for tag, payload in batch:
            if tag == "event":
                events.append(cast(ApiRequestEvent, payload))
            else:
                invocations.append(cast(McpToolInvocation, payload))

        if self.session_factory is None:
            # No storage wired (a unit test or a deliberately disabled
            # deployment): count as written so the counters stay meaningful.
            self._stats.written_events += len(events)
            self._stats.written_invocations += len(invocations)
            return

        try:
            async with self.session_factory() as session:  # type: ignore[union-attr]
                from src.infrastructure.adapters.repository.sql_api_telemetry_repo import (
                    SQLTelemetryRepository,
                )

                repository = SQLTelemetryRepository(session)
                if events:
                    await repository.save_events(events)
                if invocations:
                    await repository.save_invocations(invocations)
            self._stats.written_events += len(events)
            self._stats.written_invocations += len(invocations)
        except Exception as exc:  # noqa: BLE001 — telemetry must never propagate
            self._stats.write_failures += 1
            logger.warning("Telemetry batch write failed (%d items): %s", len(batch), exc)
            if not self._retry_buffer:
                # One retry only. A permanent failure degrades to dropped rows
                # (counted) rather than an unbounded retry buffer.
                self._stats.retried_batches += 1
                self._retry_buffer = batch[: self.batch_size]


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

#: The ingestion sink the middleware and the MCP tool wrapper share.
#:
#: A module-level singleton is the pragmatic choice here: one process must have
#: exactly one bounded queue, and it must be reachable from both the HTTP
#: middleware (which has no dependency-injection container) and the MCP tool
#: layer (which runs inside a mounted ASGI app). Tests replace it via
#: :func:`set_telemetry_ingestion`.
_INGESTION: TelemetryIngestion | None = None


def get_telemetry_ingestion() -> TelemetryIngestion:
    """The process's ingestion sink, created on first use."""
    global _INGESTION
    if _INGESTION is None:
        from src.infrastructure.config.settings import get_settings

        settings = get_settings()
        _INGESTION = TelemetryIngestion(
            batch_size=settings.TELEMETRY_INGEST_BATCH_SIZE,
            max_queue=settings.TELEMETRY_INGEST_MAX_QUEUE,
            flush_interval_seconds=settings.TELEMETRY_INGEST_FLUSH_SECONDS,
            enabled=settings.TELEMETRY_ENABLED,
        )
    return _INGESTION


def set_telemetry_ingestion(ingestion: TelemetryIngestion | None) -> None:
    """Replace the process sink (tests, or an explicitly disabled deployment)."""
    global _INGESTION
    _INGESTION = ingestion
