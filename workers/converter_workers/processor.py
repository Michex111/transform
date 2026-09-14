import asyncio
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Coroutine, Protocol

from src.infrastructure.logging.loggers import worker_logger
from workers.converter_workers.context.worker_context import WorkerContext
from workers.converter_workers.context.event_context import EventContext
from workers.converter_workers.retry import retry_on_exception
from src.domain.conversions.entities.conversion_job import ConversionJob, JobStatus
from src.domain.conversions.exceptions import InvalidStateTransition
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.policies.credit_calculator import calculate_credits
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings

try:
    from workers.converter_workers.ports import CreditPort  # type: ignore[no-redef]
except ImportError:  # pragma: no cover - added by the backend agent in parallel
    class CreditPort(Protocol):
        """Local fallback signature until CreditPort lands in ports.py."""

        async def get_remaining(self, user_id: int, period_key: str) -> int | None: ...

        async def get_tier(self, user_id: int) -> SubscriptionTier: ...

        async def consume(self, user_id: int, period_key: str, units: int) -> int: ...

type JobProcess = Callable[[WorkerContext, ConversionJob], Coroutine[None, None, None]] 

settings = get_settings()


@retry_on_exception(logger=worker_logger)
async def _download_input_file(context: WorkerContext, job: ConversionJob, input_dest: Path) -> Path:
    """
    Download the job's input object to disk and return the path of the
    plaintext file to feed to the converter.

    When encryption at rest is enabled, the stored object is normally
    ciphertext; it is decrypted into a sibling temp file before conversion.
    However, uploads are written to object storage directly from the browser
    (plaintext, no ingest-time encryption), so an object may be plaintext even
    with a master key configured. We detect the ciphertext magic and only
    decrypt when the object is actually encrypted — otherwise we pass the
    plaintext through so plaintext uploads convert correctly.
    """
    await asyncio.to_thread(context.storage_port.download, job.object_key, input_dest)
    log_context = context.get_log_context(job_id=job.job_id, conversion_type=job.conversion)
    worker_logger.debug(f"Downloaded input file for job {job.job_id} to {input_dest}", extra=log_context)

    if context.encryption_service is None:
        return input_dest

    # Client-side (FENCR) encryption takes precedence: if the browser uploaded
    # a FENCR blob we must unwrap the per-file data key and decrypt it here.
    # This is checked FIRST because a FENCR blob would otherwise be misread as
    # a plaintext object by the at-rest TRENC probe below (its magic differs,
    # but probing FENCR first keeps the ordering explicit and unambiguous).
    if job.client_encrypted and context.encryption_service.is_fencr_file(input_dest):
        if not job.data_key_wrapped:
            raise ValueError(
                f"Job {job.job_id} is client-encrypted but has no wrapped data key"
            )
        data_key = context.encryption_service.decrypt_file(
            bytes.fromhex(job.data_key_wrapped), _actor_key(job)
        )
        plain_input = input_dest.parent / f"plain_{input_dest.name}"
        await asyncio.to_thread(
            context.encryption_service.decrypt_fencr_file,
            input_dest,
            plain_input,
            data_key,
        )
        worker_logger.debug(
            f"Decrypted client-encrypted (FENCR) input for job {job.job_id} "
            f"to {plain_input}",
            extra=log_context,
        )
        return plain_input

    if not context.encryption_service.is_encrypted_file(input_dest):
        # Plaintext object (direct browser upload). No ingest-time encryption,
        # so it is already converter-ready — do not attempt to decrypt it.
        worker_logger.debug(
            f"Input file for job {job.job_id} is plaintext (not encrypted); "
            f"passing through to converter",
            extra=log_context,
        )
        return input_dest

    plain_input = input_dest.parent / f"plain_{input_dest.name}"
    await asyncio.to_thread(
        context.encryption_service.decrypt_file_to,
        input_dest,
        plain_input,
        _actor_key(job),
    )
    worker_logger.debug(
        f"Decrypted input file for job {job.job_id} to {plain_input}", extra=log_context
    )
    return plain_input

@retry_on_exception(logger=worker_logger)
async def _convert_file(context: WorkerContext, job: ConversionJob, input_file: Path, output_file: Path) -> int:
    """Run the converter and return elapsed wall-clock time in milliseconds.

    The conversion is bounded by ``WORKER_CONVERSION_TIMEOUT`` so a hung
    converter (e.g. a decompression bomb or a stuck subprocess) fails the job
    with a descriptive error instead of blocking the worker forever.
    """
    converter = context.converter_registry.get_converter(job.conversion)
    if not converter:
        raise RuntimeError(f"No converter found for conversion type {job.conversion}")

    timeout_seconds = int(getattr(settings, "WORKER_CONVERSION_TIMEOUT", 600))
    start = time.monotonic()
    await asyncio.wait_for(
        asyncio.to_thread(converter, str(input_file), str(output_file)),
        timeout=timeout_seconds,
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Guard: a converter (e.g. LibreOffice headless) can report success without
    # actually writing the output file. Verify it exists before upload so the
    # job fails here with a clear message instead of a confusing upload error.
    if not Path(output_file).is_file() or Path(output_file).stat().st_size == 0:
        raise RuntimeError(
            f"Converter produced no output file at {output_file} for job {job.job_id}"
        )

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

    The output key is namespaced by user and job so that two jobs (even across
    users) converting the same input filename never overwrite each other.
    """
    actor = _actor_key(job)
    output_dest = (
        f"{settings.BASE_TARGET_KEY.rstrip('/')}/user/{actor}/job/{job.job_id}/"
        f"{output_file.name}"
    )

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


def _period_key() -> str:
    """Monthly credit period key, mirroring src.presentation.api.routers.v1.credits."""
    return datetime.now(UTC).strftime("%Y-%m")


def _mark_failed(job: ConversionJob, message: str) -> None:
    """Force the job into FAILED regardless of the state-machine transition rules."""
    try:
        job.fail(message)
    except InvalidStateTransition:
        job.status = JobStatus.FAILED
        job.error_message = message


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

    credit_port = context.credit_port
    # Credits are only enforced for authenticated jobs when a credit port is
    # wired. Guest jobs and deployments without credit wiring skip all credit
    # logic so existing behavior is preserved.
    actor_user_id = job.user_id
    credits_enabled = credit_port is not None and actor_user_id is not None
    period_key = _period_key()
    tier = SubscriptionTier.FREE  # fallback for guests / best-effort pre-check failures
    credits_remaining: int | None = None

    try:
        job.start_processing()
        worker_logger.debug(f"Job status updated to PROCESSING for job {job.job_id}", extra=log_context)
        await _persist()

        # --- Credit pre-check: gate heavy work behind the remaining balance. ---
        # A transient credit-DB outage is non-fatal: we log a warning and proceed
        # best-effort. Only an explicit "remaining <= 0" is a hard stop.
        if credits_enabled and credit_port is not None and actor_user_id is not None:
            try:
                remaining = await credit_port.get_remaining(actor_user_id, period_key)
                tier = await credit_port.get_tier(actor_user_id)
                if remaining is not None and remaining <= 0:
                    error_message = (
                        "Conversion credits exhausted. Upgrade your plan or purchase more credits."
                    )
                    _mark_failed(job, error_message)
                    await _persist()
                    # Terminal, permanent state — do NOT retry. Return normally so the
                    # worker acks the message instead of re-queueing it.
                    await context.event_port.publish(
                        job_id=job.job_id,
                        status="FAILED",
                        progress=100,
                        message="conversion credits exhausted",
                        credits_remaining=0,
                    )
                    worker_logger.warning(
                        f"Job {job.job_id} failed: conversion credits exhausted",
                        extra=log_context,
                    )
                    return
            except Exception as e:
                worker_logger.warning(
                    f"Credit pre-check failed for job {job.job_id}: {e}", extra=log_context
                )
                tier = SubscriptionTier.FREE

        with tempfile.TemporaryDirectory() as temp_dir:
            input_file, output_file = resolve_path(job.input_file, job.conversion, Path(temp_dir))

            # Download the input file (decrypting it first when at-rest
            # encryption is enabled)
            await context.event_port.publish(**event.downloading().to_dict())
            plain_input = await _download_input_file(context, job, input_file)

            # Perform the conversion — measure actual compute time
            await context.event_port.publish(**event.processing().to_dict())
            compute_duration_ms = await _convert_file(context, job, plain_input, output_file)

            # Calculate credits from actual compute time (tier-aware discount)
            credits_used = calculate_credits(
                compute_duration_ms=compute_duration_ms,
                source_format=job.conversion.source_format,
                target_format=job.conversion.target_format,
                tier=tier,
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

            # Deduct credits AFTER a successful conversion. A consume failure is
            # non-fatal: the output already exists and the user must not lose it.
            if credits_enabled and credit_port is not None and actor_user_id is not None:
                try:
                    credits_remaining = await credit_port.consume(
                        actor_user_id, period_key, credits_used
                    )
                except Exception as e:
                    worker_logger.warning(
                        f"Failed to consume credits for job {job.job_id}: {e}", extra=log_context
                    )
                    credits_remaining = None

            completed_fields = event.completed(
                compute_duration_ms=compute_duration_ms,
                credits_used=credits_used,
            ).to_dict()
            if credits_remaining is not None:
                completed_fields["credits_remaining"] = credits_remaining
            # Persist the completed row BEFORE emitting the terminal event so
            # that a client seeing COMPLETED via SSE (and immediately fetching
            # the job to build its download URL) never observes a job that is
            # "completed" in the stream but still has no output_file persisted.
            # Previously the event was published first, causing a race where the
            # History/Queue/guest download could show "Ready" but fail to find a
            # downloadable output until a refresh.
            await _persist()
            await context.event_port.publish(**completed_fields)

    except Exception as e:
        error_message = str(e)
        _mark_failed(job, error_message)
        await _persist()
        await context.event_port.publish(**event.failed(error_message).to_dict())
        raise RuntimeError(error_message)


# Maximum length for a single path component. POSIX NAME_MAX is 255; the Minio
# download path appends a ".part.minio" suffix (10 chars) and encryption adds a
# "plain_" prefix, so we keep the base name comfortably under the limit.
_MAX_FILENAME_LEN = 180  # 180 + 10 (".part.minio") + 6 ("plain_") <= 255


def _safe_filename(name: str) -> str:
    """Return a path-component-safe, length-bounded filename.

    Long display names (e.g. e-book titles) can exceed the OS 255-char
    filename limit once the Minio SDK's ``.part.minio`` suffix (or encryption's
    ``plain_`` prefix) is added, causing ``Errno 36: File name too long``.
    Truncate to a safe length while preserving the file extension.
    """
    name = name.strip() or "file"
    if len(name) <= _MAX_FILENAME_LEN:
        return name

    # Keep the extension (if any) by splitting at the last dot.
    dot = name.rfind(".")
    if dot > 0 and dot < len(name) - 1:
        ext = name[dot:]
        stem = name[:dot]
        keep = _MAX_FILENAME_LEN - len(ext)
        return stem[:keep] + ext
    return name[:_MAX_FILENAME_LEN]


def resolve_path(
    file_location: str,
    conversion: ConversionType,
    directory: Path,
) -> tuple[Path, Path]:
    file_name = _safe_filename(Path(file_location).name)
    safe_stem = _safe_filename(Path(file_name).stem)

    output_name = safe_stem + f".{conversion.target_format}"

    downloads = directory / "downloads"
    uploads = directory / "uploads"

    downloads.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)

    input_file = downloads / file_name
    output_file = uploads / output_name

    return input_file, output_file




    

