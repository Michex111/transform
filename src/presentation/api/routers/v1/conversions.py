from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.download_stream import iter_decrypted_object
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_conversion_service,
    get_encryption_service,
    get_minio_download_adapter,
    get_transfer_service,
)
from src.presentation.schemas.conversion import (
    ConversionJobResponse,
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


@router.post("/jobs", response_model=ConversionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_conversion_job(
    payload: CreateConversionJobRequest,
    current_user: CurrentUser,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
) -> ConversionJobResponse:
    job = ConversionJob(
        job_id="",
        conversion=ConversionType(
            source_format=payload.source_format.lower().strip(),
            target_format=payload.target_format.lower().strip(),
        ),
        input_file=payload.input_key,
        user_id=current_user.id,
    )

    try:
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
