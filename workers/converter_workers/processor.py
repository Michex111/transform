import asyncio
import tempfile
import time
from pathlib import Path
from typing import Callable, Coroutine
from src.infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.context.event_context import EventContext
from workers.converter_workers.retry import retry_on_exception
from src.domain.conversions.entities.conversion_job import ConversionJob, JobStatus
from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.policies.credit_calculator import calculate_credits
from src.infrastructure.config.settings import get_settings

type JobProcess = Callable[[WorkerContext, ConversionJob], Coroutine[None, None, None]] 

settings = get_settings()


@retry_on_exception(logger=worker_logger)
async def _download_input_file(context: WorkerContext, job: ConversionJob, input_file: Path) -> Path:
    """
    Download the job's input object to disk and return the path of the
    plaintext file to feed to the converter.

    When encryption at rest is enabled, the stored object is ciphertext; it is
    decrypted into a sibling temp file before conversion.
    """
    await asyncio.to_thread(context.storage_port.download, job.input_file, input_file)
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_file}", extra=log_context)

    if context.encryption_service is None:
        return input_file

    plain_input = input_file.parent / f"plain_{input_file.name}"
    await asyncio.to_thread(
        context.encryption_service.decrypt_file_to,
        input_file,
        plain_input,
        _actor_key(job),
    )
    worker_logger.debug(
        f"Decrypted input file for job {job.job_id} to {plain_input}", extra=log_context
    )
    return plain_input

@retry_on_exception(logger=worker_logger)
async def _convert_file(context: WorkerContext, job: ConversionJob, input_file: Path, output_file: Path) -> int:
    """Run the converter and return elapsed wall-clock time in milliseconds."""
    converter = context.converter_registry.get_converter(job.conversion)
    if not converter:
        raise RuntimeError(f"No converter found for conversion type {job.conversion}")

    start = time.monotonic()
    await asyncio.to_thread(converter, str(input_file), str(output_file))
    elapsed_ms = int((time.monotonic() - start) * 1000)

    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(
        f"Processing completed for job {job.job_id}, output at {output_file}, "
        f"compute_time={elapsed_ms}ms",
        extra=log_context,
    )
    return elapsed_ms

@retry_on_exception(logger=worker_logger)
async def _upload_output_file(context: WorkerContext, job: ConversionJob, output_file: Path) -> str:
    """
    Upload the converted file to object storage and return its object key.

    When encryption at rest is enabled, the converted file is encrypted first,
    so only ciphertext is stored.
    """
    output_dest = f"{settings.BASE_TARGET_KEY.rstrip('/')}/{output_file.name}"

    upload_source = output_file
    if context.encryption_service is not None:
        encrypted_file = output_file.parent / f"{output_file.name}.enc"
        await asyncio.to_thread(
            context.encryption_service.encrypt_file_to,
            output_file,
            encrypted_file,
            _actor_key(job),
        )
        upload_source = encrypted_file

    await asyncio.to_thread(context.storage_port.upload, output_dest, upload_source)
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Uploaded output file for job {job.job_id} to {output_dest}", extra=log_context)
    return output_dest


def _actor_key(job: ConversionJob) -> str:
    """Key used to derive the per-user encryption key for a job."""
    return str(job.user_id) if job.user_id is not None else "guest"

async def process_job(context: WorkerContext, job: ConversionJob) -> None:
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.info(f"Starting processing job {job.job_id} with conversion {job.conversion}", extra=log_context)
    event = EventContext(job_id=job.job_id)

    async def _persist() -> None:
        """Persist the job's current status when a repository is configured."""
        if context.job_repository is not None:
            try:
                await context.job_repository.update_conversion_job(job)
            except Exception as e:  # never let persistence failures fail the conversion
                worker_logger.warning(
                    f"Failed to persist job {job.job_id} status: {e}", extra=log_context
                )

    try:
        job.start_processing()
        worker_logger.debug(f"Job status updated to PROCESSING for job {job.job_id}", extra=log_context)
        await _persist()

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file, output_file = resolve_path(job.input_file, job.conversion, Path(temp_dir))

            # Download the input file (decrypting it first when at-rest
            # encryption is enabled)
            await context.event_port.publish(**event.downloading().to_dict())
            plain_input = await _download_input_file(context, job, input_file)

            # Perform the conversion — measure actual compute time
            await context.event_port.publish(**event.processing().to_dict())
            compute_duration_ms = await _convert_file(context, job, plain_input, output_file)

            # Calculate credits from actual compute time
            credits_used = calculate_credits(
                compute_duration_ms=compute_duration_ms,
                source_format=job.conversion.source_format,
                target_format=job.conversion.target_format,
            )
            job.set_compute_result(duration_ms=compute_duration_ms, credits=credits_used)

            # Upload the output file
            await context.event_port.publish(**event.uploading().to_dict())
            output_dest = await _upload_output_file(context, job, output_file)

            # Update job status to COMPLETED
            job.complete(output_dest)
            worker_logger.debug(
                f"Conversion completed for job {job.job_id}, output at {output_file}, "
                f"compute_time={compute_duration_ms}ms, credits={credits_used}",
                extra=log_context,
            )
            await context.event_port.publish(
                **event.completed(compute_duration_ms=compute_duration_ms, credits_used=credits_used).to_dict(),
            )
        await _persist()

    except Exception as e:
        error_message = str(e)
        try:
            job.fail(error_message)
        except InvalidStateTransition:
            # Defensive: if the state machine rejects the transition, force FAILED.
            job.status = JobStatus.FAILED
            job.error_message = error_message
        await _persist()
        await context.event_port.publish(**event.failed(error_message).to_dict())
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




    

