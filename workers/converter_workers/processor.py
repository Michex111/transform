import asyncio
import tempfile
from pathlib import Path
from typing import Callable, Coroutine
from src.infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.context.event_context import EventContext
from workers.converter_workers.retry import retry_on_exception
from src.domain.entities.conversion_job import ConversionJob, JobStatus
from src.domain.value_object.conversion_type import ConversionType
from src.infrastructure.config.settings import get_settings

type JobProcess = Callable[[WorkerContext, ConversionJob], Coroutine[None, None, None]] 

settings = get_settings()


@retry_on_exception(logger=worker_logger)
async def _download_input_file(context: WorkerContext, job: ConversionJob, input_file: Path) -> None:
    await asyncio.to_thread(context.storage_port.download, job.input_file, input_file)
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_file}", extra=log_context)

@retry_on_exception(logger=worker_logger)
async def _convert_file(context: WorkerContext, job: ConversionJob, input_file: Path, output_file: Path) -> None:
    converter = context.converter_registry.get_converter(job.conversion)
    if not converter:
        raise RuntimeError(f"No converter found for conversion type {job.conversion}")
    
    await asyncio.to_thread(converter, str(input_file), str(output_file))
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Processing completed for job {job.job_id}, output at {output_file}", extra=log_context)

@retry_on_exception(logger=worker_logger)
async def _upload_output_file(context: WorkerContext, job: ConversionJob, output_file: Path) -> str:
    output_dest = f"s3-file_store/{output_file.name}"
    await asyncio.to_thread(context.storage_port.upload, output_dest, output_file)
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Uploaded output file for job {job.job_id} to {output_dest}", extra=log_context)
    return output_dest

async def process_job(context: WorkerContext, job: ConversionJob) -> None:
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.info(f"Starting processing job {job.job_id} with conversion {job.conversion}", extra=log_context)
    event = EventContext(job_id=job.job_id)

    try:
        job.start_processing()
        worker_logger.debug(f"Job status updated to PROCESSING for job {job.job_id}", extra=log_context)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file, output_file = resolve_path(job.input_file, job.conversion, Path(temp_dir))

            # Download the input file
            await context.event_port.publish(**event.downloading().to_dict())
            await _download_input_file(context, job, input_file)
            # Perform the conversion
            
            await context.event_port.publish(**event.processing().to_dict())
            await _convert_file(context, job, input_file, output_file)
            
            await context.event_port.publish(**event.uploading().to_dict())
            output_dest = await _upload_output_file(context, job, output_file)

            # Upload the output file
            
            # Update job status to COMPLETED
            job.complete(output_dest)
            worker_logger.debug(f"Conversion completed for job {job.job_id}, output at {output_file}", extra=log_context)
            await context.event_port.publish(**event.completed().to_dict())

    except Exception as e:
        error_message = str(e)
        job.fail(error_message)
        raise RuntimeError(error_message)



# =============================================================================== #




async def process_job_v2(context: WorkerContext, job: ConversionJob) -> None:
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.info(f"Starting processing job {job.job_id} with conversion {job.conversion}", extra=log_context)
    event = EventContext(job_id=job.job_id)

    try:
        job.start_processing()
        
        worker_logger.debug(f"Job status updated to PROCESSING for job {job.job_id}", extra=log_context)

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file, output_file = resolve_path(job.input_file, job.conversion, Path(temp_dir))

            # Download the input file
            await context.event_port.publish(**event.downloading().to_dict())
            context.storage_port.download(job.input_file, input_file)
            worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_file}", extra=log_context)

            # Perform the conversion
            converter = context.converter_registry.get_converter(job.conversion)
            if not converter:
                raise RuntimeError(f"No converter found for conversion type {job.conversion}")
            
            await context.event_port.publish(**event.processing().to_dict())
            converter(str(input_file), str(output_file))
            await context.event_port.publish(**event.uploading().to_dict())
            worker_logger.debug(f"Conversion completed for job {job.job_id}, output at {output_file}", extra=log_context)

            # Upload the output file
            output_dest = f"s3-file_store/{output_file.name}"
            context.storage_port.upload(output_dest, output_file)
            worker_logger.debug(f"Uploaded output file for job {job.job_id} to {output_dest}", extra=log_context)

            # Update job status to COMPLETED
            job.complete(output_dest)
            await context.event_port.publish(**event.completed().to_dict())

    except Exception as e:
        error_message = str(e)
        job.fail(error_message)
        raise RuntimeError(error_message)
    
#for Test purposes
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

def resolve_path(
    file_location: str,
    conversion: ConversionType,
    directory: Path,
) -> tuple[Path, Path]:
    file_name = Path(file_location).name

    output_name = (
        Path(file_name).stem +
        f".{conversion.target_format}"
    )

    downloads = directory / "downloads"
    uploads = directory / "uploads"

    downloads.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)

    input_file = downloads / file_name
    output_file = uploads / output_name

    return input_file, output_file




    

