"""API Key management endpoints — persisted in PostgreSQL (hashed)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from src.application.services.api_key_service import APIKeyService
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import get_api_key_service
from src.presentation.schemas.api_keys import (
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyListItem,
    APIKeyListResponse,
)

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _to_list_item(api_key) -> APIKeyListItem:
    return APIKeyListItem(
        id=api_key.id,
        name=api_key.name,
        prefix=APIKeyService.KEY_PREFIX,
        status=str(api_key.status),
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
    )


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: APIKeyCreateRequest,
    current_user: CurrentUser,
    service: Annotated[APIKeyService, Depends(get_api_key_service)],
) -> APIKeyCreateResponse:
    """Generate a new API key. The full key is only returned once."""
    plaintext, api_key = await service.create(
        user_id=current_user.id,
        name=payload.name,
        expires_in_days=payload.expires_in_days,
        rate_limit_per_minute=payload.rate_limit_per_minute or 100,
    )
    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        key=plaintext,
        prefix=APIKeyService.KEY_PREFIX,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get("", response_model=APIKeyListResponse)
async def list_api_keys(
    current_user: CurrentUser,
    service: Annotated[APIKeyService, Depends(get_api_key_service)],
) -> APIKeyListResponse:
    """List all API keys for the current user."""
    keys = await service.list_for_user(current_user.id)
    return APIKeyListResponse(keys=[_to_list_item(k) for k in keys])


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    current_user: CurrentUser,
    service: Annotated[APIKeyService, Depends(get_api_key_service)],
) -> None:
    """Revoke an API key."""
    api_key = await service.revoke(key_id, current_user.id)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

