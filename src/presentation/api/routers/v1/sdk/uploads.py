from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.dtos.subscription_dto import ConversionActor
from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.file_transfer_exceptions import (
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from src.application.services.conversion_access_service import ConversionAccessService
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.application.services.priority_queue_dispatcher import PriorityQueueDispatcher
from src.domain.conversions.exceptions import InvalidStateTransition
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.presentation.api.dependencies.api_key_dependencies import SdkClientPrincipal, get_sdk_client
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_access_service,
    get_conversion_repository,
    get_conversion_service,
    get_priority_queue_dispatcher,
    get_transfer_service,
)
from src.presentation.schemas.upload import CreateUploadSessionRequest


router = APIRouter(prefix="/api/v1/sdk/uploads", tags=["sdk-uploads"])


@router.post("/sessions", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
    payload: CreateUploadSessionRequest,
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadResponse:
    return await transfer_service.create_upload(
        file_extension=payload.file_extension,
        user_id=sdk_client.actor_key,
    )


@router.get("/sessions/{upload_id}", response_model=UploadSession)
async def get_upload_session(
    upload_id: str,
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadSession:
    del sdk_client
    try:
        return await transfer_service.get_upload_session(upload_id)
    except UploadSessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{upload_id}/verify", response_model=UploadSession)
async def verify_upload_session(
    upload_id: str,
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    dispatcher: Annotated[PriorityQueueDispatcher, Depends(get_priority_queue_dispatcher)],
    access_service: Annotated[ConversionAccessService, Depends(get_conversion_access_service)],
    conversion_repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    job_id: str | None = None,
    uploaded_size_bytes: int = 0,
) -> UploadSession:
    try:
        session = await transfer_service.verify_upload_completion(upload_id)
        if job_id is None:
            return session
        job = await conversion_service.get_conversion_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        if uploaded_size_bytes > 0:
            await access_service.commit_storage_usage(
                actor=ConversionActor(
                    actor_key=sdk_client.actor_key,
                    user_id=sdk_client.actor_key,
                    tier=sdk_client.tier,
                ),
                added_bytes=uploaded_size_bytes,
            )
        try:
            job.pending_processing()
        except InvalidStateTransition as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await conversion_repository.update_conversion_job(job)
        await dispatcher.dispatch(job=job, tier=sdk_client.tier)
        return session
    except UploadSessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UploadVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/sessions/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload_session(
    upload_id: str,
    sdk_client: Annotated[SdkClientPrincipal, Depends(get_sdk_client)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> Response:
    del sdk_client
    await transfer_service.delete_upload_session(upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
