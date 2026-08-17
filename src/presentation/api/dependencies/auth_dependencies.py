from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.api_key_service import APIKeyService
from src.infrastructure.auth.jwt_provider import verify_access_token
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import get_db_session


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/users/token",
    auto_error=False,
)


async def _authenticate_jwt(token: str | None, db: AsyncSession) -> UserModel:
    """Resolve the JWT bearer token to an active user."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id_int))
    user = result.scalars().first()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def _authenticate_api_key(api_key_header: str, db: AsyncSession) -> UserModel:
    """Resolve an X-API-Key header to the owning active user."""
    service = APIKeyService(SQLAPIKeyRepository(db))
    api_key = await service.authenticate(api_key_header)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    user = await db.get(UserModel, int(api_key.user_id))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return user


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: Annotated[AsyncSession | None, Depends(get_db_session)] = None,
) -> UserModel:
    """Authenticate via JWT bearer token or X-API-Key header."""
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database session unavailable",
        )
    if x_api_key:
        return await _authenticate_api_key(x_api_key, db)
    return await _authenticate_jwt(token, db)


CurrentUser = Annotated[UserModel, Depends(get_current_user)]