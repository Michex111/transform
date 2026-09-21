"""
Cleanup worker entry point.

Runs as an independent process from the converter worker. Start it with:

    uv run python -m workers.cleanup_worker.main
"""

import asyncio
import signal
import sys
from urllib.parse import urlsplit

from src.infrastructure.config.settings import get_settings
from src.infrastructure.logging.loggers import setup_worker_logging, worker_logger
from workers.cleanup_worker.dependencies import build_cleanup_worker
from workers.cleanup_worker.worker import CleanupWorker

# Namespace the cleanup worker logs under. Configuring THIS logger (instead of
# relying on the root logger) is what makes the worker's INFO lines visible:
# ``worker_logger`` attaches its handler to the named logger
# "file_converter_worker" only, and nothing configures the root logger, so its
# effective level stays WARNING and every INFO line from
# ``logging.getLogger(__name__)`` was dropped. A cleanup worker failing on every
# cycle therefore looked identical to a healthy one.
_CLEANUP_LOGGER_NAME = "workers.cleanup_worker"

_logging_configured = False


def configure_logging() -> None:
    """Give the cleanup worker's loggers a handler that emits INFO and above.

    ``workers.cleanup_worker.worker`` (and any sibling module) propagates to
    this logger, whose level is set to DEBUG, so their INFO records are created
    and emitted instead of being filtered out at the root logger.
    """
    global _logging_configured
    if _logging_configured:
        return
    setup_worker_logging(_CLEANUP_LOGGER_NAME)
    _logging_configured = True


def _redacted_database_endpoint() -> str:
    """``host:port/db`` of the configured database, with credentials stripped.

    The cleanup worker deletes real user data, so making the endpoint it talks
    to visible at startup catches a mismatched ``DATABASE_URL`` before it acts
    on the wrong database. Never logs the username or password.
    """
    raw = get_settings().DATABASE_URL.get_secret_value()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "<unparseable DATABASE_URL>"
    host = parsed.hostname or "unknown"
    endpoint = f"{host}:{parsed.port}" if parsed.port else host
    db = (parsed.path or "").lstrip("/")
    return f"{endpoint}/{db}" if db else endpoint


def _install_shutdown_handler(worker: CleanupWorker) -> None:
    """Request a graceful stop on SIGTERM and SIGINT.

    Docker's ``stop`` (and the ``restart: unless-stopped`` cycle) sends
    SIGTERM, whose default disposition terminates the interpreter immediately:
    no ``finally`` and no ``stop()``. The handler lets the in-flight cleanup
    cycle finish, then ``stop()`` wakes the inter-cycle sleep so shutdown is
    prompt instead of waiting out the interval.
    """
    loop = asyncio.get_running_loop()

    # ``add_signal_handler`` calls the callback with exactly the args passed
    # after it (here the signal number), while ``signal.signal`` passes the
    # frame — the default keeps both paths working.
    def _request_stop(signum: int, _frame: object = None) -> None:
        worker_logger.info(
            "Received signal %s; finishing the current cleanup cycle before exiting",
            signum,
        )
        worker.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _request_stop(s, f))


async def main() -> None:
    """Bootstrap the cleanup worker and run it until interrupted."""
    configure_logging()
    worker_logger.info("Initializing cleanup worker...")
    try:
        worker = build_cleanup_worker()
    except Exception as e:
        worker_logger.critical(
            f"Failed to initialize cleanup worker. Error: {str(e)}", exc_info=True
        )
        sys.exit(1)

    worker_logger.info("Starting cleanup worker")
    # One line per startup so a mismatched database is diagnosable.
    worker_logger.info(
        "Database endpoint %s | cleanup interval %ss",
        _redacted_database_endpoint(),
        get_settings().CLEANUP_INTERVAL_SECONDS,
    )

    _install_shutdown_handler(worker)

    try:
        await worker.run()
    except KeyboardInterrupt:
        worker_logger.info("Cleanup worker interrupted")
    except asyncio.CancelledError:
        worker_logger.info("Cleanup worker cancelled")
    except Exception as e:
        worker_logger.critical(f"Cleanup worker failed: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        worker.stop()
        worker_logger.info("Cleanup worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
