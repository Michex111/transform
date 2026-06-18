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
from src.infrastructure.logging.loggers import worker_logger
from src.infrastructure.adapters.storage.minio_storage import get_storage
from workers.converter_workers.dependencies import get_consumer_queue
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker
from workers.converter_workers.processor import process_job, dev_process_job
from workers.converter_workers.ports import StoragePort, QueuePort


async def build_worker(worker_name: str = "file_converter_worker") -> ConverterWorker:
    """
    Factory function to create and configure a ConverterWorker.
    
    Args:
        storage_port: Concrete implementation of StoragePort (e.g., S3Storage, LocalStorage)
        queue_port: Concrete implementation of QueuePort (e.g., SQSQueue, RedisQueue)
        worker_name: Optional worker identifier for logging
        
    Returns:
        Configured ConverterWorker instance ready to run
    """
    storage_port: StoragePort = get_storage()  # Replace with concrete implementation
    
    # TODO: Instantiate concrete QueuePort implementation
    queue_port: QueuePort = await get_consumer_queue(consumer_group="conversion-workers", consumer_name=worker_name)  # Replace with concrete implementation
    
    if storage_port is None or queue_port is None:
        raise RuntimeError(
            "StoragePort and QueuePort implementations must be configured. "
            "See TODO comments in main() for examples."
        )

    context = WorkerContext(
        storage_port=storage_port,
        queue_port=queue_port,
        converter_registry=get_registry(),
        worker_name=worker_name
    )
    
    worker = ConverterWorker(context=context, process_job=dev_process_job)
    return worker


async def main():
    """
    Main entry point to start the converter worker.
    Instantiates concrete port implementations and runs the worker.
    """
    # TODO: Instantiate concrete StoragePort implementation
    
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
