from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.application.dtos.job_views import Page
from src.application.dtos.subscription_dto import ConversionActor
from src.application.exceptions.subscription_exceptions import (
    GuestRateLimitExceeded,
    MissingActorIdentity,
)
from src.application.services.conversion_access_service import ConversionAccessService
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.application.services.user_job_query_service import UserJobQueryService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.exceptions import (
    InsufficientCredits,
    MissingCreditAccount,
    StorageQuotaExceeded,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_subscription_repo import SQLSubscriptionRepository
from src.infrastructure.converters.converter_registry import get_registry
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_access_service,
    get_conversion_repository,
    get_conversion_service,
    get_minio_url_storage,
    get_subscription_repository,
    get_transfer_service,
    get_user_job_query_service,
)
from src.presentation.schemas.conversion import (
    ConversionJobResponse,
    CreateConversionJobRequest,
    SupportedConversionResponse,
)
from src.presentation.schemas.history import (
    ActiveQueueItemResponse,
    HistoryItemResponse,
    PaginatedResponse,
)


router = APIRouter(prefix="/api/v1/web/conversions", tags=["web-conversions"])


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


async def _resolve_user_tier(
    repository: SQLSubscriptionRepository,
    user_id: int,
) -> SubscriptionTier:
    actor_key = f"user:{user_id}"
    tier = await repository.get_actor_tier(actor_key)
    if tier == SubscriptionTier.GUEST:
        return SubscriptionTier.FREE
    return tier


def _build_conversion_job(payload: CreateConversionJobRequest) -> ConversionJob:
    return ConversionJob(
        job_id="",
        conversion=ConversionType(
            source_format=payload.source_format.lower().strip(),
            target_format=payload.target_format.lower().strip(),
        ),
        input_file=payload.input_key,
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
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    access_service: Annotated[ConversionAccessService, Depends(get_conversion_access_service)],
    subscription_repository: Annotated[SQLSubscriptionRepository, Depends(get_subscription_repository)],
) -> ConversionJobResponse:
    tier = await _resolve_user_tier(subscription_repository, current_user.id)
    actor = ConversionActor(
        actor_key=f"user:{current_user.id}",
        user_id=str(current_user.id),
        tier=tier,
    )

    try:
        authorization = await access_service.authorize_conversion(
            actor=actor,
            incoming_file_size_bytes=payload.expected_file_size_bytes,
        )
        job = _build_conversion_job(payload)
        setattr(job, "user_id", current_user.id)
        await conversion_service.create_conversion_job(job)
        upload_response = await transfer_service.create_upload(job.input_file, str(current_user.id))
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


@router.post("/guest/jobs", response_model=ConversionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_guest_conversion_job(
    payload: CreateConversionJobRequest,
    request: Request,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    access_service: Annotated[ConversionAccessService, Depends(get_conversion_access_service)],
) -> ConversionJobResponse:
    guest_ip = request.client.host if request.client else "unknown"
    actor = ConversionActor(
        actor_key=f"guest:{guest_ip}",
        ip_address=guest_ip,
        tier=SubscriptionTier.GUEST,
    )

    try:
        authorization = await access_service.authorize_conversion(
            actor=actor,
            incoming_file_size_bytes=payload.expected_file_size_bytes,
        )
        job = _build_conversion_job(payload)
        await conversion_service.create_conversion_job(job)
        upload_response = await transfer_service.create_upload(job.input_file, actor.actor_key)
    except InvalidConversion as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except GuestRateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    response = _to_response(job, download_url=upload_response.upload_url)
    response.queue_stream = authorization.queue_stream
    return response


@router.get("/jobs/{job_id}", response_model=ConversionJobResponse)
async def get_conversion_job(
    job_id: str,
    current_user: CurrentUser,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    storage=Depends(get_minio_url_storage),
) -> ConversionJobResponse:
    del current_user
    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if str(job.status).upper() == "COMPLETED" and job.output_file:
        download_url = storage.generate_get_url(job.output_file, expires_in_minutes=15)

    return _to_response(job, download_url=download_url)


@router.get("/history", response_model=PaginatedResponse[HistoryItemResponse])
async def list_conversion_history(
    current_user: CurrentUser,
    query_service: Annotated[UserJobQueryService, Depends(get_user_job_query_service)],
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse[HistoryItemResponse]:
    result = await query_service.list_history(
        user_id=str(current_user.id),
        page=Page(page=page, page_size=page_size),
    )
    return PaginatedResponse[HistoryItemResponse](
        items=[HistoryItemResponse.model_validate(item.__dict__) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/active", response_model=PaginatedResponse[ActiveQueueItemResponse])
async def list_active_queue(
    current_user: CurrentUser,
    query_service: Annotated[UserJobQueryService, Depends(get_user_job_query_service)],
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse[ActiveQueueItemResponse]:
    result = await query_service.list_active_queue(
        user_id=str(current_user.id),
        page=Page(page=page, page_size=page_size),
    )
    return PaginatedResponse[ActiveQueueItemResponse](
        items=[ActiveQueueItemResponse.model_validate(item.__dict__) for item in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )
