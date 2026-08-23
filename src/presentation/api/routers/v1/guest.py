"""Guest conversion API.

Browsing, uploading, converting, streaming progress, and downloading files
**without an account**. Guests are identified only by a randomly minted
``guest_token`` (never a JWT). Every status / event / download endpoint gates
access with that token so a guessable ``job_id`` alone grants nothing.

Note: guest jobs are ownerless (``user_id=None``). The cleanup worker removes
guest jobs and their ephemeral objects once the retention window passes.
"""

import asyncio
import json
import logging
import secrets
from typing import Annotated
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse

from src.application.dtos.upload_dto import UploadResponse, UploadSession
from src.application.exceptions.conversion_job_exception import InvalidConversionJobError
from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.exceptions.file_transfer_exceptions import (
    UploadSessionNotFoundError,
    UploadVerificationError,
)
from src.application.services.conversion_service import ConversionService
from src.application.services.file_transfer_service import TransferService
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.exceptions import InvalidConversion
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.cache.redis_session_adapter import RedisSessionAdapter
from src.infrastructure.adapters.queues.redis_stream_status_queue import JobEventSubscriber
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import (
    MinioFileStorageAdapter,
    MinioUrlStorageAdapter,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.presentation.api.dependencies.download_stream import iter_decrypted_object
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_conversion_service,
    get_encryption_service,
    get_event_subscriber,
    get_guest_token_cache,
    get_minio_download_adapter,
    get_minio_url_storage,
    get_transfer_service,
)
from src.presentation.api.routers.v1.conversions import _to_response
from src.presentation.schemas.conversion import (
    ConversionMapResponse,
    ConversionJobResponse,
    CreateConversionJobRequest,
)
from src.presentation.schemas.guest import GuestJobResponse
from src.presentation.schemas.upload import CreateUploadSessionRequest

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/guest", tags=["guest"])

# Header name accepted as an alternative to the ``?guest_token=`` query param.
_GUEST_TOKEN_HEADER = "X-Guest-Token"


async def _require_guest_token(
    cache: RedisSessionAdapter,
    guest_token: str | None,
    job_id: str,
) -> None:
    """Validate that ``guest_token`` maps to ``job_id``.

    A missing token is a client error (401); a present-but-wrong token is a
    forbidden condition (403). A token that maps to a different job is treated
    as forbidden (403), which prevents job-ID enumeration.
    """
    if not guest_token or not guest_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A guest_token is required",
        )

    stored_job_id = await cache.get(guest_token.strip())
    if not stored_job_id or stored_job_id != job_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or mismatched guest token",
        )


@router.get("/conversions/supported/map", response_model=ConversionMapResponse)
async def list_conversion_map() -> ConversionMapResponse:
    """Return a map of every supported source format to its valid target formats.

    This is unauthenticated so the guest page can render valid source → target
    choices before the user uploads anything.
    """
    return ConversionMapResponse(conversions=build_conversion_map())


@router.post(
    "/conversions/jobs",
    response_model=GuestJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_conversion_job(
    payload: CreateConversionJobRequest,
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    cache: Annotated[RedisSessionAdapter, Depends(get_guest_token_cache)],
) -> GuestJobResponse:
    """Create a guest conversion job and mint its access token."""
    if not payload.source_format or not payload.input_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_format and input_key are required for guest conversions",
        )

    try:
        job = ConversionJob(
            job_id="",
            conversion=ConversionType(
                source_format=payload.source_format.lower().strip(),
                target_format=payload.target_format.lower().strip(),
            ),
            input_file=payload.input_key,
            user_id=None,
        )
        await conversion_service.create_conversion_job(job)

        guest_token = secrets.token_urlsafe(32)
        ttl = timedelta(hours=get_settings().GUEST_JOB_RETENTION_HOURS)
        await cache.set(guest_token, job.job_id, ttl=ttl)

    except InvalidConversion as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidConversionJobError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    base = _to_response(job)
    return GuestJobResponse(
        **base.model_dump(),
        guest_token=guest_token,
    )


@router.post(
    "/uploads/sessions",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_session(
    payload: CreateUploadSessionRequest,
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
) -> UploadResponse:
    """Create a guest upload session with no folder and no owner."""
    return await transfer_service.create_upload(
        file_extension=payload.file_extension,
        user_id="guest",
        file_name=payload.file_name,
    )


@router.post("/uploads/sessions/{upload_id}/verify", response_model=UploadSession)
async def verify_upload_session(
    upload_id: str,
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    conversion_service: Annotated[ConversionService, Depends(get_conversion_service)],
    storage: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    cache: Annotated[RedisSessionAdapter, Depends(get_guest_token_cache)],
    job_id: str = Query(...),
    guest_token: str | None = Query(default=None),
    x_guest_token: str | None = Header(default=None, alias=_GUEST_TOKEN_HEADER),
) -> UploadSession:
    """Finalize a guest upload, enforce the guest size limit, and enqueue it."""
    await _require_guest_token(cache, guest_token or x_guest_token, job_id)
    settings = get_settings()

    try:
        session = await transfer_service.verify_upload_completion(upload_id)

        # Enforce the guest-tier size limit before pointing the job at the
        # uploaded object (guest files are ephemeral and never persisted to a
        # user library).
        stats = await storage.stat_object(session.object_key) or {}
        size = int(stats.get("size", 0))
        if size > settings.GUEST_MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the guest maximum size "
                       f"({settings.GUEST_MAX_FILE_SIZE // (1024 * 1024)} MB).",
            )

        job = await conversion_service.get_conversion_job(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

        # Point the job at the object that was actually uploaded and enqueue it
        # at the GUEST tier (AWAITING_UPLOAD -> PENDING transition + publish).
        job.object_key = session.object_key
        await conversion_service.push_conversion_job(job, tier=SubscriptionTier.GUEST)

        return session

    except UploadSessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UploadVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except FileSystemError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/conversions/jobs/{job_id}", response_model=ConversionJobResponse)
async def get_conversion_job(
    job_id: str,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    encryption_service: Annotated[FileEncryptionService | None, Depends(get_encryption_service)],
    cache: Annotated[RedisSessionAdapter, Depends(get_guest_token_cache)],
    guest_token: str | None = Query(default=None),
    x_guest_token: str | None = Header(default=None, alias=_GUEST_TOKEN_HEADER),
) -> ConversionJobResponse:
    """Return a guest conversion job, gated by the guest token."""
    await _require_guest_token(cache, guest_token or x_guest_token, job_id)

    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    download_url = None
    if str(job.status).lower() == "completed" and job.output_file:
        if encryption_service is not None:
            download_url = f"/api/guest/conversions/jobs/{job_id}/download"
        else:
            download_url = await transfer_service.create_download_url(
                job.output_file, expires_in_minutes=15
            )

    return _to_response(job, download_url=download_url)


@router.get(
    "/conversions/jobs/{job_id}/download",
    response_model=None,
    responses={302: {"description": "Redirect to the pre-signed download URL"}},
)
async def download_conversion_output(
    job_id: str,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    storage: Annotated[MinioFileStorageAdapter, Depends(get_minio_download_adapter)],
    encryption_service: Annotated[FileEncryptionService, Depends(get_encryption_service)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    cache: Annotated[RedisSessionAdapter, Depends(get_guest_token_cache)],
    guest_token: str | None = Query(default=None),
    x_guest_token: str | None = Header(default=None, alias=_GUEST_TOKEN_HEADER),
) -> StreamingResponse | RedirectResponse:
    """Stream (or redirect to) the output of a completed guest job."""
    await _require_guest_token(cache, guest_token or x_guest_token, job_id)

    job = await repository.get_conversion_job(job_id)
    if job is None or str(job.status).lower() != "completed" or not job.output_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job output not found")

    # No encryption → hand the browser a pre-signed GET URL so it streams
    # directly from object storage (guest jobs carry no per-user key).
    if encryption_service is None:
        download_url = await transfer_service.create_download_url(
            job.output_file, expires_in_minutes=15
        )
        return RedirectResponse(url=download_url, status_code=status.HTTP_302_FOUND)

    # Encryption enabled → decrypt on the fly with the fixed "guest" actor key
    # (the job's user_id is None).
    return StreamingResponse(
        iter_decrypted_object(storage, job.output_file, encryption_service, "guest"),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{job.output_file.split("/")[-1]}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/events/jobs/{job_id}")
async def stream_job_events(
    job_id: str,
    request: Request,
    repository: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
    subscriber: Annotated[JobEventSubscriber, Depends(get_event_subscriber)],
    cache: Annotated[RedisSessionAdapter, Depends(get_guest_token_cache)],
    guest_token: str | None = Query(default=None),
    x_guest_token: str | None = Header(default=None, alias=_GUEST_TOKEN_HEADER),
):
    """SSE stream of guest conversion progress (no ownership check)."""
    token = guest_token or x_guest_token
    await _require_guest_token(cache, token, job_id)

    job = await repository.get_conversion_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    async def event_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'job_id': job_id, 'status': 'connected'})}\n\n"

            terminal_seen = False
            async for _, fields in subscriber.iter_events(job_id):
                if await request.is_disconnected():
                    return
                status_value = fields.get("status", "")
                payload = {
                    "job_id": job_id,
                    "status": status_value,
                    "progress": fields.get("progress", 0),
                    "message": fields.get("message", ""),
                }
                if "compute_duration_ms" in fields:
                    payload["compute_duration_ms"] = fields["compute_duration_ms"]
                if "credits_used" in fields:
                    payload["credits_used"] = fields["credits_used"]
                if "credits_remaining" in fields:
                    payload["credits_remaining"] = fields["credits_remaining"]

                yield f"event: progress\ndata: {json.dumps(payload)}\n\n"

                if status_value in ("COMPLETED", "FAILED"):
                    terminal_seen = True
                    break

            if not terminal_seen:
                while not await request.is_disconnected():
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(15)

        except asyncio.CancelledError:
            logger.info("SSE connection cancelled for guest job %s", job_id)
        except Exception as e:  # pragma: no cover - defensive
            logger.error("SSE error for guest job %s: %s", job_id, e)
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
