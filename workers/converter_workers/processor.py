import tempfile
from pathlib import Path
from typing import Callable
from infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from domain.entities.conversion_job import ConversionJob, JobStatus

JobProcess = Callable[[WorkerContext, ConversionJob], None]

def process_job(context: WorkerContext, job: ConversionJob) -> None:
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.info(f"Starting processing job {job.job_id} with conversion {job.conversion}", extra=log_context)

    try:
        job.start_processing()
        worker_logger.debug(f"Job status updated to PROCESSING for job {job.job_id}", extra=log_context)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input_file"
            output_path = Path(temp_dir) / "output_file"

            # Download the input file
            context.storage_port.download(job.input_file, str(input_path))
            worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_path}", extra=log_context)

            # Perform the conversion
            converter = context.converter_registry.get_converter(job.conversion)
            if not converter:
                raise RuntimeError(f"No converter found for conversion type {job.conversion}")
            
            converter(str(input_path), str(output_path))
            worker_logger.debug(f"Conversion completed for job {job.job_id}, output at {output_path}", extra=log_context)

            # Upload the output file
            output_destination = f"converted/{output_path.name}"
            context.storage_port.upload(str(output_path), output_destination)
            worker_logger.debug(f"Uploaded output file for job {job.job_id} to {output_destination}", extra=log_context)

            # Update job status to COMPLETED
            job.complete(output_destination)
            context.queue_port.acknowledge_job(job.job_id)
            worker_logger.info(f"Job {job.job_id} completed successfully", extra=log_context)

    except Exception as e:
        error_message = str(e)
        job.fail(error_message)
        context.queue_port.fail_job(job.job_id, error_message)
        worker_logger.error(f"Job {job.job_id} failed with error: {error_message}", extra=log_context)