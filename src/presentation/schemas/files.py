"""File management API schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class FileMetadataResponse(BaseModel):
    id: str
    file_name: str
    file_key: str
    file_size_bytes: int
    mime_type: str
    folder_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    is_favorite: bool = False


class FileListResponse(BaseModel):
    files: list[FileMetadataResponse]
    total: int
    page: int
    page_size: int


class FileDownloadResponse(BaseModel):
    download_url: str
    expires_in_seconds: int = 900  # 15 minutes


class RenameFileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class FavoriteFileRequest(BaseModel):
    is_favorite: bool


class BatchDeleteRequest(BaseModel):
    file_ids: list[str] = []
    folder_ids: list[str] = []


class BatchDeleteResponse(BaseModel):
    deleted_files: int
    deleted_folders: int


class PresignedUrlRequest(BaseModel):
    object_key: str
    expiry_seconds: int = Field(default=900, ge=60, le=86400)


class PresignedUrlResponse(BaseModel):
    url: str
    object_key: str
    expires_in_seconds: int


class PresignedUrlsRequest(BaseModel):
    object_keys: list[str] = Field(min_length=1, max_length=50)
    expiry_seconds: int = Field(default=900, ge=60, le=86400)
