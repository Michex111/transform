"""Entry point for the workspace worker.

Run with ``uv run python -m workers.workspace_worker.main``.

This is the only process that may talk to the runtime provider's daemon. The API
cannot: the deployed API container has no Docker socket, so anything that needs
to create, start or exec into a container happens here. That is also why a
workspace feature that "works in the UI" but has no worker running shows durable
QUEUED executions rather than pretending the work was done.

SIGTERM/SIGINT install a "finish the current unit, then exit" handler rather than
dying mid-unit, which is the most likely way to lose a real run.
"""

import asyncio
import logging
import socket
import sys
from contextlib import suppress

from src.infrastructure.logging.loggers import worker_logger
from workers.workspace_worker.consumer import WorkspaceWorkConsumer
from workers.workspace_worker.dependencies import build_context
from workers.workspace_worker.worker import WorkspaceWorker


def _default_worker_name() -> str:
    """A unique consumer identity per process.

    Shared identities are not harmless: two processes with one name share a
    pending list, so one's stale sweep can steal the other's in-flight unit and
    run it twice — and, in this feature, run a user's program twice.
    """
    return f"workspace_worker-{socket.gethostname()}-{__import__('os').getpid()}"


def _install_shutdown_handler(worker: WorkspaceWorker) -> None:
    import signal

    loop = asyncio.get_running_loop()

    def _request_stop(*_args: object) -> None:
        worker_logger.warning("Shutdown requested; finishing the current unit")
        worker.stop()

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - platform
            # The fallback callback must accept an argument or signal.signal
            # raises TypeError when it calls the handler.
            signal.signal(sig, lambda *_a, **_k: _request_stop())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    context = build_context()
    consumer = await WorkspaceWorkConsumer.create(
        redis_client=context.redis_client,
        consumer_group=context.consumer_group,
        consumer_name=_default_worker_name(),
        execution_timeout_seconds=context.execution_timeout_seconds,
    )
    worker = WorkspaceWorker(context=context, consumer=consumer)
    _install_shutdown_handler(worker)

    worker_logger.warning(
        "Workspace worker: provider=%s, group=%s",
        context.provider.name,
        context.consumer_group,
    )
    try:
        await worker.run()
    finally:
        with suppress(Exception):
            await consumer.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # pragma: no cover - operator interrupt
        sys.exit(130)
