"""
Worker bootstrap entry point.
Orchestrates dependency injection and runs the converter worker.
"""

import sys

from infrastructure.converters.converter_registry import converter_registry
from infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.worker import ConverterWorker
from workers.converter_workers.processor import process_job
from workers.converter_workers.port_dependencies import StoragePort, QueuePort


def create_worker(storage_port: StoragePort, queue_port: QueuePort, worker_name: str = "file_converter_worker") -> ConverterWorker:
    """
    Factory function to create and configure a ConverterWorker.
    
    Args:
        storage_port: Concrete implementation of StoragePort (e.g., S3Storage, LocalStorage)
        queue_port: Concrete implementation of QueuePort (e.g., SQSQueue, RedisQueue)
        worker_name: Optional worker identifier for logging
        
    Returns:
        Configured ConverterWorker instance ready to run
    """
    context = WorkerContext(
        storage_port=storage_port,
        queue_port=queue_port,
        converter_registry=converter_registry,
        worker_name=worker_name
    )
    
    worker = ConverterWorker(context=context, process_job=process_job)
    return worker


def main():
    """
    Main entry point to start the converter worker.
    Instantiates concrete port implementations and runs the worker.
    """
    # TODO: Instantiate concrete StoragePort implementation
    # Example: storage_port = S3Storage(bucket="my-bucket")
    # Example: storage_port = LocalStorage(base_path="/data")
    storage_port: StoragePort = None  # Replace with concrete implementation
    
    # TODO: Instantiate concrete QueuePort implementation
    # Example: queue_port = SQSQueue(queue_url="https://...")
    # Example: queue_port = RedisQueue(host="localhost", port=6379)
    queue_port: QueuePort = None  # Replace with concrete implementation
    
    if storage_port is None or queue_port is None:
        raise RuntimeError(
            "StoragePort and QueuePort implementations must be configured. "
            "See TODO comments in main() for examples."
        )
    
    worker = create_worker(storage_port=storage_port, queue_port=queue_port)
    
    log_context = worker.context.get_log_context()
    worker_logger.info("Starting converter worker", extra=log_context)
    
    try:
        worker.run()
    except KeyboardInterrupt:
        worker_logger.info("Worker interrupted", extra=log_context)
    except Exception as e:
        worker_logger.critical(f"Worker failed: {str(e)}", extra=log_context)
        sys.exit(1)
    finally:       
        worker_logger.info("Converter worker stopped", extra=log_context)


if __name__ == "__main__":
    main()
