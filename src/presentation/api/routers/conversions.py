from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.application.services.file_transfer_service import TransferService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.services.conversion_service import ConversionService
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_conversion_service,
    get_minio_url_storage,
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
    file_transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> ConversionJobResponse:
    
    job = ConversionJob(
        job_id="",
        conversion=ConversionType(
            source_format=payload.source_format.lower().strip(),
            target_format=payload.target_format.lower().strip(),
        ),
        input_file=payload.input_key,
    )

    try:
        await conversion_service.create_conversion_job(job)
        upload_response = await file_transfer_service.create_upload(job.input_file, str(current_user.id))
    except InvalidConversion as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidConversionJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return _to_response(job, download_url=upload_response.upload_url)


@router.get("/jobs/{job_id}", response_model=ConversionJobResponse)
async def get_conversion_job(
    job_id: str,
    current_user: CurrentUser,
    repository=Depends(get_conversion_repository),
    storage=Depends(get_minio_url_storage),
) -> ConversionJobResponse:
    del current_user
    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if str(job.status).lower() == "completed" and job.output_file:
        download_url = storage.generate_get_url(job.output_file, expires_in_minutes=15)

    return _to_response(job, download_url=download_url)
