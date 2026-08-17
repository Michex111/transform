"""
Cleanup worker entry point.

Runs as an independent process from the converter worker. Start it with:

    uv run python -m workers.cleanup_worker.main
"""

import asyncio
import sys

from src.infrastructure.logging.loggers import worker_logger
from workers.cleanup_worker.dependencies import build_cleanup_worker


async def main() -> None:
    """Bootstrap the cleanup worker and run it until interrupted."""
    worker_logger.info("Initializing cleanup worker...")
    try:
        worker = build_cleanup_worker()
    except Exception as e:
        worker_logger.critical(
            f"Failed to initialize cleanup worker. Error: {str(e)}", exc_info=True
        )
        sys.exit(1)

    worker_logger.info("Starting cleanup worker")
    try:
        await worker.run()
    except KeyboardInterrupt:
        worker_logger.info("Cleanup worker interrupted")
    except Exception as e:
        worker_logger.critical(f"Cleanup worker failed: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        worker.stop()
        worker_logger.info("Cleanup worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
