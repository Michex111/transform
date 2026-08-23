from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.services.conversion_service import ConversionService
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.download_stream import iter_decrypted_object
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_conversion_service,
    get_encryption_service,
    get_file_service,
    get_minio_download_adapter,
    get_transfer_service,
)
from src.presentation.schemas.conversion import (
    ConversionHistoryResponse,
    ConversionJobResponse,
    ConversionMapResponse,
    CreateConversionJobRequest,
    SupportedConversionResponse,
)


router = APIRouter(prefix="/api/conversions", tags=["conversions"])


def _to_response(job: ConversionJob, download_url: str | None = None) -> ConversionJobResponse:
    return ConversionJobResponse(
        job_id=job.job_id or "",
        status=str(job.status),
        source_format=job.conversion.source_format,
        target_format=job.conversion.target_format,
        input_file=job.input_file,
        output_file=job.output_file,
        object_key=job.object_key or None,
        download_url=download_url,
        error_message=job.error_message,
        credits_used=job.credits_used,
        compute_duration_ms=job.compute_duration_ms,
    )


@router.get("/supported", response_model=list[SupportedConversionResponse])
async def list_supported_conversions() -> list[SupportedConversionResponse]:
    conversions = sorted(
        get_registry().list_conversions(),
        key=lambda item: (item.source_format, item.target_format),
    )
    return [
        SupportedConversionResponse(
            source_format=item.source_format,
            target_format=item.target_format,
        )
        for item in conversions
    ]


@router.get("/supported/map", response_model=ConversionMapResponse)
async def list_conversion_map() -> ConversionMapResponse:
    """Return a map of every source format to its valid target formats."""
    return ConversionMapResponse(conversions=build_conversion_map())


@router.get("/history", response_model=ConversionHistoryResponse)
async def list_conversion_history(
    current_user: CurrentUser,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    range: str | None = Query(default=None, description="Time filter: 24h | 7d | 30d; omitting returns all history"),
) -> ConversionHistoryResponse:
    """Return the current user's conversion history (newest first)."""
    offset = (page - 1) * page_size
    rows, total = await conversion_service.list_history(
        current_user.id, offset=offset, limit=page_size, since=_history_since(range),
    )
    return ConversionHistoryResponse(
        jobs=[_to_response(j) for j in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def _history_since(range_value: str | None) -> datetime | None:
    """Map a ``range`` query param to a ``created_at`` lower bound (UTC)."""
    if not range_value:
        return None
    now = datetime.now(UTC)
    range_key = range_value.strip().lower()
    mapping = {
        "24h": timedelta(hours=24),
        "last_day": timedelta(hours=24),
        "7d": timedelta(days=7),
        "last_week": timedelta(days=7),
        "30d": timedelta(days=30),
        "last_month": timedelta(days=30),
    }
    delta = mapping.get(range_key)
    return now - delta if delta is not None else None


@router.delete("/history/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversion_history(
    job_id: str,
    current_user: CurrentUser,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
) -> Response:
    """Delete a single conversion-history record owned by the current user."""
    deleted = await conversion_service.delete_history_job(job_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/jobs", response_model=ConversionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_conversion_job(
    payload: CreateConversionJobRequest,
    current_user: CurrentUser,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> ConversionJobResponse:
    try:
        if payload.file_id:
            # Library path: convert a file the user already uploaded.
            try:
                file = await file_service.get_file(current_user.id, payload.file_id)
            except FileSystemError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

            source_format = Path(file.file_name).suffix.lstrip(".").lower()
            target_format = payload.target_format.lower().strip()
            if not source_format:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Could not infer a source format from the file name",
                )
            job = await conversion_service.convert_library_file(
                file_name=file.file_name,
                source_format=source_format,
                target_format=target_format,
                object_key=file.file_key,
                user_id=current_user.id,
            )
            return _to_response(job)

        # Upload flow: source_format/input_key are required.
        if not payload.source_format or not payload.input_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="source_format and input_key are required when file_id is not provided",
            )

        job = ConversionJob(
            job_id="",
            conversion=ConversionType(
                source_format=payload.source_format.lower().strip(),
                target_format=payload.target_format.lower().strip(),
            ),
            input_file=payload.input_key,
            user_id=current_user.id,
        )
        await conversion_service.create_conversion_job(job)

    except InvalidConversion as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidConversionJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return _to_response(job)


@router.get("/jobs/{job_id}", response_model=ConversionJobResponse)
async def get_conversion_job(
    job_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    encryption_service: Annotated[FileEncryptionService | None, Depends(get_encryption_service)],
) -> ConversionJobResponse:
    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Authenticated users may only inspect their own jobs.
    if job.user_id is not None and job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if str(job.status).lower() == "completed" and job.output_file:
        if encryption_service is not None:
            # The stored object is ciphertext; serve it decrypted via the API.
            download_url = f"/api/conversions/jobs/{job_id}/download"
        else:
            # Pre-signed GET URL via the transfer service + storage URL gateway.
            download_url = await transfer_service.create_download_url(
                job.output_file, expires_in_minutes=15
            )

    return _to_response(job, download_url=download_url)


@router.post("/jobs/{job_id}/retry", response_model=ConversionJobResponse)
async def retry_conversion_job(
    job_id: str,
    current_user: CurrentUser,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
) -> ConversionJobResponse:
    """Retry a failed job without re-uploading its input file.

    The input object is already in object storage at the job's ``object_key``,
    so retrying just resets the job to PENDING and re-enqueues it. The worker
    downloads the existing file and tries again.
    """
    try:
        job = await conversion_service.retry_conversion_job(job_id, current_user.id)
    except InvalidConversionJobError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return _to_response(job)


@router.get("/jobs/{job_id}/download")
async def download_conversion_output(
    job_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    storage: Annotated[MinioFileStorageAdapter, Depends(get_minio_download_adapter)],
    encryption_service: Annotated[FileEncryptionService, Depends(get_encryption_service)],
) -> StreamingResponse:
    """Stream the decrypted output of a completed job (encryption-enabled)."""
    if encryption_service is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is only available when encryption is enabled",
        )

    job = await repository.get_conversion_job(job_id)
    if job is None or str(job.status).lower() != "completed" or not job.output_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job output not found")

    # Ownership check: authenticated users may only read their own outputs.
    if job.user_id is not None and job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    actor_key = str(job.user_id) if job.user_id is not None else "guest"
    return StreamingResponse(
        iter_decrypted_object(storage, job.output_file, encryption_service, actor_key),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{job.output_file.split("/")[-1]}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
