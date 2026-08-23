"""Folder-related API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.presentation.schemas.files import FileMetadataResponse


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = Field(default=None, description="Parent folder, or None for a root folder")


class RenameFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MoveFileRequest(BaseModel):
    folder_id: str | None = Field(default=None, description="Destination folder, or None to move to root")


class MoveFolderRequest(BaseModel):
    parent_id: str | None = Field(
        default=None,
        description="Destination parent folder, or None to move to root",
    )


class FolderResponse(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FolderListResponse(BaseModel):
    folders: list[FolderResponse]
    total: int
    page: int
    page_size: int


class FolderContentsResponse(BaseModel):
    folder: FolderResponse
    folders: list[FolderResponse]
    files: list[FileMetadataResponse]
    total_folders: int
    total_files: int
