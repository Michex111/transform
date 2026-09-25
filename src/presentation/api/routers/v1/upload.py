from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.exceptions.file_transfer_exceptions import (
	InvalidPartNumberError,
	MissingUploadPartsError,
	UploadNotMultipartError,
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
from src.presentation.schemas.upload import (
	CreateUploadSessionRequest,
	UploadPartUrl,
	UploadPartUrlsRequest,
	UploadPartUrlsResponse,
	UploadVerifyRequest,
)


router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("/sessions", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
	payload: CreateUploadSessionRequest,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
	file_service: Annotated[FileService, Depends(get_file_service)],
) -> UploadResponse:
	"""Create an upload session.

	When the client declares ``file_size`` the request is checked against the
	per-file cap and the account's storage quota BEFORE a session exists, so a
	too-large upload is refused without transferring a single byte. That check
	is advisory (it trusts a client-supplied number); the authoritative check
	runs at finalize against the size the object actually has. See
	``FileService.authorize_upload_size`` / ``complete_upload``.
	"""
	try:
		if payload.folder_id is not None:
			await file_service.get_folder(current_user.id, payload.folder_id)
		max_file_size_bytes = await file_service.authorize_upload_size(
			current_user.id, payload.file_size
		)
	except FileSystemError as exc:
		raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc

	return await transfer_service.create_upload(
		file_extension=payload.file_extension,
		user_id=str(current_user.id),
		file_name=payload.file_name,
		folder_id=payload.folder_id,
		file_size=payload.file_size,
		max_file_size_bytes=max_file_size_bytes,
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


@router.post("/sessions/{upload_id}/parts", response_model=UploadPartUrlsResponse)
async def create_upload_part_urls(
	upload_id: str,
	payload: UploadPartUrlsRequest,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadPartUrlsResponse:
	"""Mint presigned PUT URLs for one batch of parts of a multipart upload.

	Part URLs are deliberately short-lived and minted in batches: the client
	asks for the parts it is about to send, so the window only has to cover one
	batch rather than the whole multi-gigabyte transfer. The response echoes the
	window so the client can re-request a batch rather than fail mid-upload.
	"""
	try:
		session = await transfer_service.get_upload_session(upload_id)
	except UploadSessionNotFoundError as exc:
		# 410, not 404: the cache cannot distinguish "never existed" from
		# "expired", and the only recovery for either is to start a new session.
		raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc

	if session.user_id is not None and session.user_id != str(current_user.id):
		raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found")

	try:
		parts = await transfer_service.generate_part_urls(session, payload.part_numbers)
	except UploadNotMultipartError as exc:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
	except InvalidPartNumberError as exc:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
		) from exc

	return UploadPartUrlsResponse(
		parts=[UploadPartUrl(part_number=number, url=url) for number, url in parts],
		expires_in_minutes=transfer_service.large_ttl_minutes,
	)


@router.post("/sessions/{upload_id}/verify", response_model=UploadSession)
async def verify_upload_session(
	upload_id: str,
	current_user: CurrentUser,
	transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
	conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
	file_service: Annotated[FileService, Depends(get_file_service)],
	payload: UploadVerifyRequest | None = None,
	job_id: str | None = None,
) -> UploadSession:
	"""Finalize an upload: complete multipart if needed, then run every check.

	This is the single finalize endpoint on purpose — the size, type and quota
	checks all live behind it, so there is no second path that could skip one.
	The body is optional: a single-PUT session sends nothing, a multipart
	session sends its part etags (which is what lets the provider assemble the
	object).
	"""
	try:
		parts = (
			[(part.part_number, part.etag) for part in payload.parts]
			if payload is not None and payload.parts
			else None
		)
		session = await transfer_service.verify_upload_completion(upload_id, parts=parts)

		# The session must belong to the caller (unless it is a guest session
		# created without a user).
		if session.user_id is not None and session.user_id != str(current_user.id):
			raise UploadSessionNotFoundError("Upload session not found")

		# Enforce the per-file cap and the storage quota, then persist the file
		# record. A rejection here also removes the object, so a refused upload
		# leaves no orphan behind.
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
	except (MissingUploadPartsError, InvalidPartNumberError) as exc:
		raise HTTPException(
			status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
		) from exc
	except (UploadVerificationError, UploadNotMultipartError) as exc:
		raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
	except FileSystemError as exc:
		# ``http_detail()`` is a dict for the upload-limit rejections (the SPA
		# branches on its ``code``) and the message string for everything else.
		raise HTTPException(status_code=exc.status_code, detail=exc.http_detail()) from exc


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
	# The service aborts an in-flight multipart upload as part of the delete, so
	# abandoning a large upload releases its parts instead of leaving them for a
	# bucket lifecycle rule.
	await transfer_service.delete_upload_session(upload_id)
	return Response(status_code=status.HTTP_204_NO_CONTENT)
