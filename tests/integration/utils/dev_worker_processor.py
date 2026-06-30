from pathlib import Path

from domain.entities.conversion_job import ConversionJob
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.processor import resolve_path
from domain.entities.conversion_job import ConversionJob

from infrastructure.logging.loggers import worker_logger


def dev_process_job(context: WorkerContext, job: ConversionJob) -> None:
    log_context = context.get_log_context(job.job_id, job.conversion)
    worker_logger.info(f"starting dev processing for {job.job_id} with conversion {job.conversion}")

    try:
        job.start_processing()
        worker_logger.debug(f"job status updated to processing for job {job.job_id}")


        dir = Path("converter_storage")
        input_file, output_file = resolve_path(job.input_file, job.conversion, dir)

        context.storage_port.download(job.input_file, input_file)
        worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_file}", extra=log_context)

        converter = context.converter_registry.get_converter(job.conversion)
        if not converter:
            raise RuntimeError(f"No converter found for conversion type {job.conversion}")
        
        converter(str(input_file), str(output_file))
        worker_logger.debug(f"Conversion completed for job {job.job_id}, output at {output_file}", extra=log_context)

        output_dest = f"s3-file_store/{output_file.name}"
        context.storage_port.upload(output_dest, output_file)

        worker_logger.debug(f"Uploaded output file for job {job.job_id} to {output_dest}", extra=log_context)

            # Update job status to COMPLETED
        job.complete(output_dest)

    except Exception as e:
        error_message = str(e)
        job.fail(error_message)
        raise RuntimeError(error_message)