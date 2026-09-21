"""
Worker bootstrap entry point.
Orchestrates dependency injection and runs the converter worker.
"""

import asyncio
import os
import signal
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Add project root to sys.path to enable imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.infrastructure.adapters.queues.redis_stream_job_queue import JobStreamConsumer
from src.infrastructure.converters.converter_registry import get_registry
from workers.converter_workers.ports import QueuePort, StoragePort
from src.infrastructure.config.settings import get_settings
from src.infrastructure.logging.loggers import worker_logger
from src.infrastructure.adapters.storage.minio_storage_factory import get_storage
from workers.converter_workers.dependencies import (
    get_consumer_queue,
    get_credit_port,
    get_encryption_service,
    get_event_queue,
    get_job_repository,
)
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker
from workers.converter_workers.processor import process_job


def _default_worker_name() -> str:
    """Return a consumer name that is unique to this worker process.

    Redis Streams identify consumers by name, so a fixed name collapses every
    replica (and every restart) into a single consumer identity. The group's
    `pending`/`idle` figures then become aggregates across processes, and the
    stale-job reclaimer can steal messages that a live replica is still
    processing. Host + PID keeps each process distinct.

    Set WORKER_NAME to override with a stable name when required.
    """
    override = os.getenv("WORKER_NAME")
    if override:
        return override
    return f"file_converter_worker-{socket.gethostname()}-{os.getpid()}"


def _redacted_redis_endpoint() -> str:
    """``host:port/db`` of the configured Redis, with credentials stripped.

    Logging the endpoint makes a ``REDIS_URL`` mismatch between the API and the
    workers diagnosable: without it every job lands in a stream nobody consumes
    and nothing looks wrong. Usernames and passwords are never included.
    """
    raw = get_settings().REDIS_URL.get_secret_value()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<unparseable REDIS_URL>"
    host = parsed.hostname or "unknown"
    endpoint = f"{host}:{parsed.port}" if parsed.port else host
    db = (parsed.path or "").lstrip("/")
    return f"{endpoint}/{db}" if db else endpoint


def _install_shutdown_handler(worker: ConverterWorker) -> None:
    """Request a graceful stop on SIGTERM and SIGINT.

    Docker's ``stop`` (and the ``restart: unless-stopped`` cycle) sends
    SIGTERM, whose default disposition terminates the interpreter immediately:
    no ``finally``, no Redis close, and the in-flight job is left un-ACKed. The
    handler only flips the worker's run flag, so the job currently being
    processed still runs to completion and is ACKed before the loop exits
    ("finish the current job, then exit"). A job killed before it finishes is
    recovered by the stale-pending sweep rather than lost. SIGINT (Ctrl-C in a
    TTY) takes the same path.
    """
    loop = asyncio.get_running_loop()

    # ``add_signal_handler`` calls the callback with exactly the args passed
    # after it (here the signal number), while ``signal.signal`` passes the
    # frame — the default keeps both paths working.
    def _request_stop(signum: int, _frame: object = None) -> None:
        worker_logger.info(
            "Received signal %s; finishing the current job before exiting", signum
        )
        worker.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig)
        except (NotImplementedError, RuntimeError):
            # Platform/event loop without add_signal_handler (e.g. Windows):
            # signal.signal still runs the same graceful path.
            signal.signal(sig, lambda s, f: _request_stop(s, f))


async def _close_queue_client(worker: ConverterWorker) -> None:
    """Close the consumer's Redis connection so shutdown does not leak it."""
    client = getattr(worker.context.queue_port, "redis_client", None)
    if client is None:
        return
    try:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            await aclose()
        else:  # pragma: no cover - older redis-py
            client.close()
    except Exception as e:  # noqa: BLE001 — shutdown must not raise
        worker_logger.warning(f"Failed to close the Redis client cleanly: {e}")


async def build_worker(worker_name: str | None = None) -> ConverterWorker:
    """
    Factory function to create and configure a ConverterWorker.

    Args:
        worker_name: Optional worker identifier. Defaults to a name unique to
            this process so replicas never share a Redis consumer identity.

    Returns:
        Configured ConverterWorker instance ready to run
    """
    worker_name = worker_name or _default_worker_name()
    storage_port: StoragePort = get_storage()
    # WORKER_CONSUMER_GROUP was defined and documented but read by nothing, so
    # the group name existed only as a literal here. Read the setting — its
    # default is the same literal, so an unset value behaves exactly as before.
    consumer_group = get_settings().WORKER_CONSUMER_GROUP
    queue_port: QueuePort = await get_consumer_queue(
        consumer_group=consumer_group, consumer_name=worker_name
    )
    event_port = get_event_queue()
    job_repository = get_job_repository()
    credit_port = get_credit_port()
    # Enable at-rest encryption when ENCRYPTION_MASTER_KEY is configured so the
    # worker decrypts inputs before conversion and re-encrypts outputs, keeping
    # downloads (which stream through the API's decrypt path) consistent.
    encryption_service = get_encryption_service()

    if storage_port is None or queue_port is None or event_port is None:
        raise RuntimeError(
            "StoragePort, QueuePort, and JobEventPort implementations must be configured. "
            "See dependencies.py for the concrete implementations."
        )

    context = WorkerContext(
        storage_port=storage_port,
        queue_port=queue_port,
        event_port=event_port,
        converter_registry=get_registry(),
        worker_name=worker_name,
        job_repository=job_repository,
        encryption_service=encryption_service,
        credit_port=credit_port,
    )

    worker = ConverterWorker(context=context, process_job=process_job)
    return worker


async def main():
    """
    Main entry point to start the converter worker.
    Instantiates concrete port implementations and runs the worker.
    """
    worker_logger.info("Initializing converter worker...")
    try:
        worker = await build_worker()
    except Exception as e:
        worker_logger.critical(
            f"Failed to initialize worker. Ensure Redis is running (default: localhost:6379). Error: {str(e)}",
            exc_info=True
        )
        sys.exit(1)
    
    log_context = worker.context.get_log_context()
    worker_logger.info("Starting converter worker", extra=log_context)
    # One line that makes a REDIS_URL mismatch visible: a worker connected to
    # the wrong Redis otherwise looks identical to a healthy one.
    worker_logger.info(
        "Redis endpoint %s | consumer group '%s' | streams: %s",
        _redacted_redis_endpoint(),
        get_settings().WORKER_CONSUMER_GROUP,
        ", ".join(JobStreamConsumer.STREAMS),
        extra=log_context,
    )

    _install_shutdown_handler(worker)

    try:
       await worker.run()
    except KeyboardInterrupt:
        worker_logger.info("Worker interrupted", extra=log_context)
    except asyncio.CancelledError:
        # worker.run() re-raises cancellation after stopping cleanly.
        worker_logger.info("Worker cancelled", extra=log_context)
    except Exception as e:
        worker_logger.critical(f"Worker failed: {str(e)}", extra=log_context, exc_info=True)
        sys.exit(1)
    finally:       
        await _close_queue_client(worker)
        worker_logger.info("Converter worker stopped", extra=log_context)


if __name__ == "__main__":
    asyncio.run(main())
