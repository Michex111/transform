"""The worker loop.

Small on purpose: it fetches, processes, and settles the message. Everything that
decides *what* happens lives in ``processor.py``, and everything that decides
*who* gets the message lives in ``consumer.py``.

Two behaviours are copied from the converter worker because both fixed a real
message-loss bug there:

* ``CancelledError`` is re-raised **after** cleanup rather than swallowed — a
  shutdown must not look like a completed unit of work.
* a failed unit is dead-lettered *and* acknowledged, in that order, so the record
  of the failure survives and the entry leaves the pending list.
"""

import asyncio
from typing import Any, Awaitable, Callable

from src.infrastructure.logging.loggers import worker_logger
from workers.workspace_worker.consumer import WorkspaceWorkConsumer
from workers.workspace_worker.processor import process_work

type WorkProcessor = Callable[[Any, Any], Awaitable[None]]

#: How often a stale sweep runs while the queue is idle. Long enough that the
#: sweep is not itself load; short enough that a crashed run is recovered in
#: minutes rather than at the next restart.
_STALE_SWEEP_INTERVAL_SECONDS = 300


class WorkspaceWorker:
    def __init__(
        self,
        *,
        context: Any,
        consumer: WorkspaceWorkConsumer,
        process: WorkProcessor = process_work,
    ) -> None:
        self._context = context
        self._consumer = consumer
        self._process = process
        self._running = True
        self._idle_sweeps = 0

    def stop(self) -> None:
        """Ask the loop to finish the current unit and exit."""
        self._running = False

    async def run(self) -> None:
        worker_logger.info(
            "Workspace worker started; consuming %s", self._consumer.stream_name
        )
        while self._running:
            try:
                work = await self._consumer.fetch()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - the loop must survive
                worker_logger.error("Could not read from the queue: %s", exc)
                await asyncio.sleep(5)
                continue

            if work is None:
                await self._maybe_sweep_stale()
                continue

            await self._handle(work)

        worker_logger.info("Workspace worker stopped")

    async def _handle(self, work: Any) -> None:
        try:
            await self._process(self._context, work.message)
        except asyncio.CancelledError:
            # Shutdown mid-unit: leave the entry pending so the stale sweep
            # re-queues it, and re-raise so the loop exits as cancelled.
            worker_logger.warning(
                "Interrupted while processing %s; it will be re-queued",
                work.message_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - any failure is dead-lettered
            worker_logger.error(
                "Workspace work %s failed: %s", work.message_id, exc, exc_info=True
            )
            await self._consumer.fail(work.message_id, f"{exc.__class__.__name__}: {exc}")
            return
        await self._consumer.acknowledge(work.message_id)

    async def _maybe_sweep_stale(self) -> None:
        """Re-queue work abandoned by a worker that died mid-unit."""
        self._idle_sweeps += 1
        if self._idle_sweeps % max(
            1, _STALE_SWEEP_INTERVAL_SECONDS // 10
        ):
            # The fetch already blocks for ~10s, so the modulo turns the sweep
            # into roughly the configured interval without a second timer.
            return
        try:
            reclaimed = await self._consumer.reclaim_stale()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            worker_logger.warning("Stale sweep failed: %s", exc)
            return
        if reclaimed:
            worker_logger.info("Re-queued %s abandoned workspace unit(s)", reclaimed)
