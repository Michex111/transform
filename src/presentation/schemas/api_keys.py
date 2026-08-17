"""API Key management schemas."""

from datetime import datetime
from pydantic import BaseModel, Field


class APIKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)
    rate_limit_per_minute: int | None = Field(default=100, ge=1, le=10000)


class APIKeyCreateResponse(BaseModel):
    id: str
    name: str
    key: str  # Plaintext key - only returned once!
    prefix: str
    created_at: datetime
    expires_at: datetime | None = None
    message: str = "Store this key securely. You will not be able to see it again."


class APIKeyListItem(BaseModel):
    id: str
    name: str
    prefix: str
    status: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class APIKeyListResponse(BaseModel):
    keys: list[APIKeyListItem]
