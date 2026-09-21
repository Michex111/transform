from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.exceptions.file_transfer_exceptions import (
	UploadSessionNotFoundError,
	UploadVerificationError,
)
from src.application.services.conversion_service import ConversionService
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.job_access import assert_job_owner
from src.presentation.api.dependencies.service_dependencies import (
	get_conversion_service,
	get_file_service,
	get_transfer_service,
)
from src.presentation.schemas.upload import CreateUploadSessionRequest


router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/sessions", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
	payload: CreateUploadSessionRequest,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
	file_service: Annotated[FileService, Depends(get_file_service)],
) -> UploadResponse:
	if payload.folder_id is not None:
		try:
			await file_service.get_folder(current_user.id, payload.folder_id)
		except FileSystemError as exc:
			raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
	return await transfer_service.create_upload(
		file_extension=payload.file_extension,
		user_id=str(current_user.id),
		file_name=payload.file_name,
		folder_id=payload.folder_id,
	)


@router.get("/sessions/{upload_id}", response_model=UploadSession)
async def get_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadSession:
	try:
		session = await transfer_service.get_upload_session(upload_id)
	except UploadSessionNotFoundError as exc:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
	if session.user_id is not None and session.user_id != str(current_user.id):
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")
	return session


@router.post("/sessions/{upload_id}/verify", response_model=UploadSession)
async def verify_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
	conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
	file_service: Annotated[FileService, Depends(get_file_service)],
	job_id: str | None = None,
) -> UploadSession:
	try:
		session = await transfer_service.verify_upload_completion(upload_id)

		# The session must belong to the caller (unless it is a guest session
		# created without a user).
		if session.user_id is not None and session.user_id != str(current_user.id):
			raise UploadSessionNotFoundError("Upload session not found")

		# Enforce the tier size limit and persist the file record.
		await file_service.complete_upload(current_user.id, session)

		if job_id is None:
			return session
		job = await conversion_service.get_conversion_job(job_id)
		if job is not None:
			# The job must belong to the caller: without this check any
			# authenticated user could re-point and re-enqueue another user's
			# (or an ownerless guest) job via the job_id query param.
			assert_job_owner(job, current_user.id)
			# Point the job at the object key that was actually uploaded so the
			# worker reads the correct file.
			job.object_key = session.object_key
			await conversion_service.update_conversion_job(job)
			await conversion_service.push_conversion_job(job)
		return session

	except UploadSessionNotFoundError as exc:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
	except UploadVerificationError as exc:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
	except FileSystemError as exc:
		raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.delete("/sessions/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> Response:
	try:
		session = await transfer_service.get_upload_session(upload_id)
	except UploadSessionNotFoundError as exc:
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
	if session.user_id is not None and session.user_id != str(current_user.id):
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")
	await transfer_service.delete_upload_session(upload_id)
	return Response(status_code=status.HTTP_204_NO_CONTENT)
