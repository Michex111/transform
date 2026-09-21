"""File management API endpoints — backed by Minio/S3 and PostgreSQL.

Provides both file operations (upload/list/download/delete) and a folder
hierarchy so signed-in users can organise files like online file storage.
Business rules live in ``FileService``; this router stays presentation-only.
"""

from typing import Annotated

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.services.file_service import FileService
from src.application.services.file_transfer_service import TransferService
from src.infrastructure.adapters.repository.sql_conversion_job_repo import SQLConversionJobRepository
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import (
    MinioFileStorageAdapter,
    MinioUrlStorageAdapter,
)
from src.infrastructure.adapters.storage.sanitize import (
    UnsafeObjectKeyError,
    sanitize_object_key,
)
from src.infrastructure.logging.audit import log_data_access
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.download_stream import iter_decrypted_object
from src.presentation.api.dependencies.service_dependencies import (
    get_conversion_repository,
    get_encryption_service,
    get_file_service,
    get_minio_download_adapter,
    get_minio_url_storage,
    get_transfer_service,
    get_user_file_repository,
)
from src.application.dtos.upload_dto import UploadResponse
from src.presentation.schemas.files import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    FavoriteFileRequest,
    FileDownloadResponse,
    FileListResponse,
    FileMetadataResponse,
    PresignedUrlsRequest,
    PresignedUrlResponse,
    RenameFileRequest,
)
from src.presentation.schemas.folders import (
    CreateFolderRequest,
    FolderContentsResponse,
    FolderListResponse,
    FolderResponse,
    MoveFileRequest,
    MoveFolderRequest,
    RenameFolderRequest,
)
from src.presentation.schemas.upload import CreateUploadSessionRequest

router = APIRouter(prefix="/api/v1/files", tags=["files"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_metadata(row) -> FileMetadataResponse:
    return FileMetadataResponse(
        id=row.id,
        file_name=row.file_name,
        file_key=row.file_key,
        file_size_bytes=row.file_size_bytes,
        mime_type=row.mime_type,
        folder_id=row.folder_id,
        created_at=row.created_at,
        expires_at=row.expires_at,
        is_favorite=row.is_favorite,
    )


def _to_folder(row) -> FolderResponse:
    return FolderResponse(
        id=row.id,
        name=row.name,
        parent_id=row.parent_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _map_fs_error(exc: FileSystemError) -> HTTPException:
    """Map a FileService error to its HTTP status."""
    return HTTPException(status_code=exc.status_code, detail=str(exc))


# ---------------------------------------------------------------------------
# folder endpoints (declared before /{file_id} so literal segments match)
# ---------------------------------------------------------------------------

@router.post("/folders", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: CreateFolderRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FolderResponse:
    """Create a folder, optionally nested inside a parent folder."""
    try:
        folder = await file_service.create_folder(
            user_id=current_user.id,
            name=payload.name,
            parent_id=payload.parent_id,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_folder(folder)


@router.get("/folders", response_model=FolderListResponse)
async def list_root_folders(
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FolderListResponse:
    """List the user's root-level folders."""
    offset = (page - 1) * page_size
    rows, total = await file_service.list_root_folders(
        current_user.id, offset=offset, limit=page_size,
    )
    return FolderListResponse(
        folders=[_to_folder(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/folders/{folder_id}", response_model=FolderContentsResponse)
async def get_folder_contents(
    folder_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> FolderContentsResponse:
    """List the subfolders and files directly inside a folder."""
    try:
        folder, subfolders, total_folders, files, total_files = (
            await file_service.get_folder_contents(
                current_user.id, folder_id, offset=(page - 1) * page_size, limit=page_size,
            )
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return FolderContentsResponse(
        folder=_to_folder(folder),
        folders=[_to_folder(f) for f in subfolders],
        files=[_to_metadata(f) for f in files],
        total_folders=total_folders,
        total_files=total_files,
    )


@router.patch("/folders/{folder_id}", response_model=FolderResponse)
async def rename_folder(
    folder_id: str,
    payload: RenameFolderRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FolderResponse:
    """Rename a folder."""
    try:
        folder = await file_service.rename_folder(
            current_user.id, folder_id, payload.name,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_folder(folder)


@router.post("/folders/{folder_id}/move", response_model=FolderResponse)
async def move_folder(
    folder_id: str,
    payload: MoveFolderRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FolderResponse:
    """Move a folder under a new parent (or to root when ``parent_id`` is
    None). Moving a folder into itself or one of its descendants is rejected."""
    try:
        folder = await file_service.move_folder(
            current_user.id, folder_id, payload.parent_id,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_folder(folder)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> Response:
    """Delete a folder recursively: all descendant folders and files are
    removed from both the database and object storage."""
    try:
        await file_service.delete_folder(current_user.id, folder_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# file endpoints
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
    payload: CreateUploadSessionRequest,
    current_user: CurrentUser,
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> UploadResponse:
    """Create a new file upload session. Returns a pre-signed PUT URL for Minio."""
    try:
        if payload.folder_id is not None:
            await file_service.get_folder(current_user.id, payload.folder_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc

    return await transfer_service.create_upload(
        file_extension=payload.file_extension,
        user_id=str(current_user.id),
        file_name=payload.file_name,
        folder_id=payload.folder_id,
    )


@router.post("/urls", response_model=list[PresignedUrlResponse])
async def generate_presigned_urls(
    payload: PresignedUrlsRequest,
    current_user: CurrentUser,
    storage: Annotated[MinioUrlStorageAdapter, Depends(get_minio_url_storage)],
    file_repo: Annotated[SQLUserFileRepository, Depends(get_user_file_repository)],
    job_repo: Annotated[SQLConversionJobRepository, Depends(get_conversion_repository)],
) -> list[PresignedUrlResponse]:
    """Generate pre-signed GET (download) URLs for the caller's own objects.

    A key is only presigned when it belongs to the caller: one of their
    ``user_files.file_key`` rows, or a ``conversion_jobs.object_key`` /
    ``output_file``. Anything else is a 404, so the endpoint can be used neither
    to read another tenant's object nor to enumerate the bucket via the
    404/200 difference. The first unusable key still aborts the whole batch.
    """
    # Sanitize first — an unsafe key can never be one of the caller's objects.
    safe_keys: list[str] = []
    for key in payload.object_keys:
        try:
            safe_keys.append(sanitize_object_key(key))
        except UnsafeObjectKeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Object not found: {key}",
            ) from None

    # Ownership: one query per repository instead of a probe per key.
    owned = await file_repo.list_owned_keys(current_user.id, safe_keys)
    owned |= await job_repo.list_owned_object_keys(current_user.id, safe_keys)

    # Existence: one storage round-trip per *owned* unique key, in parallel.
    # Keys the caller does not own are never probed, so their presence cannot
    # be inferred from timing or from which 404 is returned.
    keys_to_check = [key for key in dict.fromkeys(safe_keys) if key in owned]
    checked = (
        await asyncio.gather(*(storage.object_exists(key) for key in keys_to_check))
        if keys_to_check
        else []
    )
    existence = dict(zip(keys_to_check, checked))

    results: list[PresignedUrlResponse] = []
    for key in safe_keys:
        if key not in owned or not existence.get(key, False):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Object not found: {key}",
            )
        url = storage.generate_get_url(key, expires_in_minutes=payload.expiry_seconds // 60)
        results.append(
            PresignedUrlResponse(
                url=url,
                object_key=key,
                expires_in_seconds=payload.expiry_seconds,
            )
        )
    return results


@router.get("", response_model=FileListResponse)
async def list_files(
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    folder_id: str | None = Query(default=None, description="Filter to files inside this folder; omit for root"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FileListResponse:
    """List files belonging to the current user, optionally filtered by folder."""
    try:
        rows, total = await file_service.list_files(
            current_user.id, folder_id, offset=(page - 1) * page_size, limit=page_size,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return FileListResponse(
        files=[_to_metadata(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/favorites", response_model=FileListResponse)
async def list_favorite_files(
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> FileListResponse:
    """List the current user's favorite files (newest first)."""
    rows, total = await file_service.list_favorite_files(
        current_user.id, offset=(page - 1) * page_size, limit=page_size,
    )
    return FileListResponse(
        files=[_to_metadata(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete(
    payload: BatchDeleteRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> BatchDeleteResponse:
    """Delete multiple files and/or folders in one call."""
    try:
        deleted_files = await file_service.delete_files(current_user.id, payload.file_ids)
        deleted_folders = await file_service.delete_folders(current_user.id, payload.folder_ids)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return BatchDeleteResponse(
        deleted_files=deleted_files,
        deleted_folders=deleted_folders,
    )


@router.patch("/{file_id}/favorite", response_model=FileMetadataResponse)
async def set_file_favorite(
    file_id: str,
    payload: FavoriteFileRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FileMetadataResponse:
    """Set or clear the favorite flag on a file."""
    try:
        updated = await file_service.set_file_favorite(
            current_user.id, file_id, payload.is_favorite,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_metadata(updated)


@router.post("/{file_id}/move", response_model=FileMetadataResponse)
async def move_file(
    file_id: str,
    payload: MoveFileRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FileMetadataResponse:
    """Move a file into a folder (or to root when folder_id is None)."""
    try:
        updated = await file_service.move_file(
            current_user.id, file_id, payload.folder_id,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_metadata(updated)


@router.patch("/{file_id}", response_model=FileMetadataResponse)
async def rename_file(
    file_id: str,
    payload: RenameFileRequest,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FileMetadataResponse:
    """Rename a file's display name. The stored object key is unchanged."""
    try:
        updated = await file_service.rename_file(
            current_user.id, file_id, payload.name,
        )
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_metadata(updated)


@router.get("/{file_id}", response_model=FileMetadataResponse)
async def get_file_metadata(
    file_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> FileMetadataResponse:
    """Get metadata for a specific file owned by the current user."""
    try:
        row = await file_service.get_file(current_user.id, file_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
    return _to_metadata(row)


@router.get("/{file_id}/download", response_model=FileDownloadResponse)
async def download_file(
    file_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    transfer_service: Annotated[TransferService, Depends(get_transfer_service)],
    encryption_service: Annotated[FileEncryptionService | None, Depends(get_encryption_service)],
) -> FileDownloadResponse:
    """Generate a time-limited download URL for the file.

    Uses the transfer service + storage URL gateway to create a pre-signed GET
    URL the frontend can download directly. When encryption at rest is enabled
    the stored object is ciphertext, so the file is instead served decrypted
    through the API streaming endpoint.
    """
    try:
        row = await file_service.get_file(current_user.id, file_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc

    log_data_access(
        user_id=str(current_user.id),
        action="download",
        resource="library_file",
        file_id=file_id,
    )
    if encryption_service is not None:
        return FileDownloadResponse(
            download_url=f"/api/v1/files/{file_id}/stream",
            expires_in_seconds=900,
        )

    expiry_minutes = 15
    url = await transfer_service.create_download_url(
        row.file_key, expires_in_minutes=expiry_minutes
    )
    return FileDownloadResponse(
        download_url=url,
        expires_in_seconds=expiry_minutes * 60,
    )


@router.get("/{file_id}/stream")
async def stream_file(
    file_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
    storage: Annotated[MinioFileStorageAdapter, Depends(get_minio_download_adapter)],
    encryption_service: Annotated[FileEncryptionService, Depends(get_encryption_service)],
) -> StreamingResponse:
    """Stream a file's decrypted contents (used when encryption is enabled)."""
    if encryption_service is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Streaming is only available when encryption is enabled",
        )

    try:
        row = await file_service.get_file(current_user.id, file_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc

    log_data_access(
        user_id=str(current_user.id),
        action="download",
        resource="library_file",
        file_id=file_id,
    )
    return StreamingResponse(
        iter_decrypted_object(storage, row.file_key, encryption_service, str(current_user.id)),
        media_type=row.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{row.file_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    current_user: CurrentUser,
    file_service: Annotated[FileService, Depends(get_file_service)],
) -> None:
    """Delete a file from both Minio/S3 and the database."""
    try:
        await file_service.delete_file(current_user.id, file_id)
    except FileSystemError as exc:
        raise _map_fs_error(exc) from exc
