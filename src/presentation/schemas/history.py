from datetime import datetime

from pydantic import BaseModel, Field


class HistoryItemResponse(BaseModel):
    """History row returned to frontend clients."""

    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    output_file: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    download_url: str | None = None


class ActiveQueueItemResponse(BaseModel):
    """Active queue row returned to frontend clients."""

    job_id: str
    status: str
    source_format: str
    target_format: str
    input_file: str
    created_at: datetime


class PaginatedResponse[T](BaseModel):
    """Generic pagination response for list endpoints."""

    items: list[T]
    total: int
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
