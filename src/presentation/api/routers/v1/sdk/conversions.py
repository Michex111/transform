from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.application.dtos.subscription_dto import ConversionActor
from src.application.exceptions.subscription_exceptions import (
    GuestRateLimitExceeded,
    MissingActorIdentity,
)
from src.application.services.conversion_access_service import ConversionAccessService
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.exceptions import (
    InsufficientCredits,
    MissingCreditAccount,
    StorageQuotaExceeded,
)
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.api_key_dependencies import SdkClientPrincipal, get_sdk_client
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_access_service,
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


router = APIRouter(prefix="/api/v1/sdk/conversions", tags=["sdk-conversions"])


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
async def list_supported_conversions(
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
) -> list[SupportedConversionResponse]:
    del sdk_client
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
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    access_service: Annotated[ConversionAccessService, Depends(get_conversion_access_service)],
) -> ConversionJobResponse:
    actor = ConversionActor(
        actor_key=sdk_client.actor_key,
        user_id=sdk_client.actor_key,
        tier=sdk_client.tier,
    )
    job = ConversionJob(
        job_id="",
        conversion=ConversionType(
            source_format=payload.source_format.lower().strip(),
            target_format=payload.target_format.lower().strip(),
        ),
        input_file=payload.input_key,
    )

    try:
        authorization = await access_service.authorize_conversion(
            actor=actor,
            incoming_file_size_bytes=payload.expected_file_size_bytes,
        )
        await conversion_service.create_conversion_job(job)
        upload_response = await transfer_service.create_upload(job.input_file, sdk_client.actor_key)
    except InvalidConversion as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (InsufficientCredits, MissingCreditAccount, StorageQuotaExceeded) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (GuestRateLimitExceeded, MissingActorIdentity) as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    response = _to_response(job, download_url=upload_response.upload_url)
    response.queue_stream = authorization.queue_stream
    response.credits_remaining = authorization.credits_remaining
    return response


@router.get("/jobs/{job_id}", response_model=ConversionJobResponse)
async def get_conversion_job(
    job_id: str,
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    storage=Depends(get_minio_url_storage),
) -> ConversionJobResponse:
    del sdk_client
    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if str(job.status).upper() == "COMPLETED" and job.output_file:
        download_url = storage.generate_get_url(job.output_file, expires_in_minutes=15)

    return _to_response(job, download_url=download_url)
