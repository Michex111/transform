"""
Worker bootstrap entry point.
Orchestrates dependency injection and runs the converter worker.
"""

import sys
from pathlib import Path
import asyncio

# Add project root to sys.path to enable imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.infrastructure.converters.converter_registry import get_registry
from workers.converter_workers.ports import QueuePort, StoragePort
from src.infrastructure.logging.loggers import worker_logger
from src.infrastructure.adapters.storage.minio_storage_factory import get_storage
from workers.converter_workers.dependencies import (
    get_consumer_queue,
    get_encryption_service,
    get_event_queue,
    get_job_repository,
)
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker
from workers.converter_workers.processor import process_job


async def build_worker(worker_name: str = "file_converter_worker") -> ConverterWorker:
    """
    Factory function to create and configure a ConverterWorker.
    
    Args:
        worker_name: Optional worker identifier for logging
        
    Returns:
        Configured ConverterWorker instance ready to run
    """
    storage_port: StoragePort = get_storage()
    queue_port: QueuePort = await get_consumer_queue(consumer_group="conversion-workers", consumer_name=worker_name)
    event_port = get_event_queue()
    job_repository = get_job_repository()
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
    
    try:
       await worker.run()
    except KeyboardInterrupt:
        worker_logger.info("Worker interrupted", extra=log_context)
    except Exception as e:
        worker_logger.critical(f"Worker failed: {str(e)}", extra=log_context, exc_info=True)
        sys.exit(1)
    finally:       
        worker_logger.info("Converter worker stopped", extra=log_context)


if __name__ == "__main__":
    asyncio.run(main())
