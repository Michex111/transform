"""
Billing worker entry point.

Runs as an independent process from the converter and cleanup workers. Start it
with:

    uv run python -m workers.billing_worker.main

The sweep only reads and updates ``user_subscriptions`` / ``monthly_credits``;
it never calls Stripe and never cancels a subscription. Entitlement is also
enforced on read, so this process being down can delay *persistence* of a
revocation but can never extend access past the deadline.
"""

import asyncio
import signal

from src.infrastructure.config.settings import get_settings
from src.infrastructure.logging.loggers import setup_worker_logging, worker_logger
from workers.billing_worker.dependencies import build_billing_worker
from workers.billing_worker.worker import BillingWorker

#: Configure THIS logger instead of relying on the root logger. ``worker_logger``
#: attaches its handler to the named "file_converter_worker" logger only, so
#: without this every INFO line from ``logging.getLogger(__name__)`` would be
#: filtered out at the root level — making a dead sweeper indistinguishable from
#: a healthy one.
_BILLING_LOGGER_NAME = "workers.billing_worker"

_logging_configured = False


def configure_logging() -> None:
    """Give the billing worker's loggers a handler that emits INFO and above."""
    global _logging_configured
    if _logging_configured:
        return
    setup_worker_logging(_BILLING_LOGGER_NAME)
    _logging_configured = True


def _install_shutdown_handler(worker: BillingWorker) -> None:
    """Request a graceful stop on SIGTERM and SIGINT.

    The default disposition of SIGTERM terminates the interpreter immediately,
    with no ``finally`` and no ``stop()``. This lets the in-flight sweep finish
    and wakes the inter-cycle sleep so shutdown is prompt.
    """
    loop = asyncio.get_running_loop()

    def _request_stop(signum: int, _frame: object = None) -> None:
        worker_logger.info(
            "Received signal %s; finishing the current sweep before exiting", signum
        )
        worker.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda s, f: _request_stop(s, f))


async def main() -> None:
    """Bootstrap the billing worker and run it until interrupted."""
    configure_logging()
    worker_logger.info("Initializing billing worker...")
    try:
        worker = build_billing_worker()
    except Exception as exc:
        worker_logger.critical(
            f"Failed to initialize billing worker. Error: {exc}", exc_info=True
        )
        raise

    settings = get_settings()
    worker_logger.info(
        "Billing worker ready: grace=%d business days (tz=%s), sweep every %ds",
        settings.PAYMENT_GRACE_BUSINESS_DAYS,
        settings.PAYMENT_GRACE_TIMEZONE,
        settings.PAYMENT_GRACE_SWEEP_INTERVAL_SECONDS,
    )

    _install_shutdown_handler(worker)
    await worker.run()
    worker_logger.info("Billing worker exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
