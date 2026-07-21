from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.services.conversion_service import ConversionService
from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.file_transfer_exceptions import (
	UploadSessionNotFoundError,
	UploadVerificationError,
)
from src.application.services.file_transfer_service import TransferService
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import get_transfer_service, get_conversion_service
from src.presentation.schemas.upload import CreateUploadSessionRequest


router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/sessions", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
	payload: CreateUploadSessionRequest,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadResponse:
	return await transfer_service.create_upload(
		file_extension=payload.file_extension,
		user_id=str(current_user.id),
	)


@router.get("/sessions/{upload_id}", response_model=UploadSession)
async def get_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadSession:
	del current_user
	try:
		return await transfer_service.get_upload_session(upload_id)
	except UploadSessionNotFoundError as exc:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{upload_id}/verify", response_model=UploadSession)
async def verify_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
	conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
	job_id: str | None = None,
) -> UploadSession:
	del current_user
	try:
		session = await transfer_service.verify_upload_completion(upload_id)
		if job_id is None:
			return session
		job = await conversion_service.get_conversion_job(job_id)
		if job:
			await conversion_service.push_conversion_job(job)
		return session

            
	except UploadSessionNotFoundError as exc:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
	except UploadVerificationError as exc:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/sessions/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> Response:
	del current_user
	await transfer_service.delete_upload_session(upload_id)
	return Response(status_code=status.HTTP_204_NO_CONTENT)
