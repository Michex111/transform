from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.api_key_service import APIKeyService
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.infrastructure.auth.jwt_provider import verify_access_token
from src.infrastructure.adapters.repository.sql_api_key_repo import SQLAPIKeyRepository
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import get_db_session
from src.infrastructure.logging.audit import (
    log_auth_failure,
    log_auth_success,
    set_audit_context,
)
from src.presentation.api.middleware.api_telemetry import (
    STATE_ACCOUNT_ID,
    STATE_API_KEY_ID,
)


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/users/token",
    auto_error=False,
)


def _record_telemetry_attribution(
    request: Request, *, account_id: int, api_key_id: str | None
) -> None:
    """Publish the verified owner on the request for the telemetry middleware.

    Written from **inside** the authentication dependency, so the value is by
    construction the identity that was actually verified. The capture
    middleware reads it after the response is produced; nothing a caller sends
    can influence it, which is what makes API-key attribution trustworthy rather
    than self-reported.
    """
    setattr(request.state, STATE_ACCOUNT_ID, account_id)
    setattr(request.state, STATE_API_KEY_ID, api_key_id)


async def _authenticate_jwt(token: str | None, db: AsyncSession, request: Request) -> UserModel:
    """Resolve the JWT bearer token to an active user."""
    if not token:
        log_auth_failure("missing_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_access_token(token)
    if user_id is None:
        log_auth_failure("invalid_or_expired_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        log_auth_failure("malformed_token_subject")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id_int))
    user = result.scalars().first()

    if user is None or not user.is_active:
        log_auth_failure("inactive_or_missing_user", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    set_audit_context(correlation_id=getattr(user, "id", None) and str(user.id), actor=str(user.id))
    log_auth_success(user_id=user_id, method="jwt")
    _record_telemetry_attribution(request, account_id=user.id, api_key_id=None)
    return user


async def _authenticate_api_key(
    api_key_header: str, db: AsyncSession, request: Request
) -> UserModel:
    """Resolve an X-API-Key header to the owning active user."""
    service = APIKeyService(SQLAPIKeyRepository(db))
    api_key = await service.authenticate(api_key_header)
    if api_key is None:
        log_auth_failure("invalid_or_expired_api_key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    user = await db.get(UserModel, int(api_key.user_id))
    if user is None or not user.is_active:
        log_auth_failure("inactive_or_missing_user", user_id=api_key.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    set_audit_context(correlation_id=str(user.id), actor=str(user.id))
    log_auth_success(user_id=api_key.user_id, method="api_key")
    _record_telemetry_attribution(request, account_id=user.id, api_key_id=api_key.id)
    return user


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: Annotated[AsyncSession | None, Depends(get_db_session)] = None,
) -> UserModel:
    """Authenticate via JWT bearer token or X-API-Key header.

    ``request`` is required (not defaulted) and comes first: FastAPI injects the
    connection, and a bare parameter cannot follow a defaulted one. It is used
    only to publish attribution for the telemetry middleware.
    """
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database session unavailable",
        )
    if x_api_key:
        return await _authenticate_api_key(x_api_key, db, request)
    return await _authenticate_jwt(token, db, request)


CurrentUser = Annotated[UserModel, Depends(get_current_user)]


async def get_request_origin(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> JobOrigin:
    """Report **how** the request authenticated, for job-origin tracking.

    Mirrors the precedence inside :func:`get_current_user` exactly: an
    ``X-API-Key`` header wins over the bearer token, so a request that presents
    both is an ``API`` request because that is the credential the API actually
    used. This deliberately duplicates no authentication — it reads the same
    header and performs no lookup, no validation and no I/O, so it adds nothing
    to the request cost and cannot reject anyone. Invalid credentials are still
    rejected by ``get_current_user``, which every endpoint here also depends on.

    Only two outcomes are possible at this layer. ``GUEST`` is never produced by
    a header — guest endpoints have no ``CurrentUser`` and set it literally.
    """
    return JobOrigin.API if x_api_key else JobOrigin.WEB


RequestOrigin = Annotated[JobOrigin, Depends(get_request_origin)]