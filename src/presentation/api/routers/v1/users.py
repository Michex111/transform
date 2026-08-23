from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.jwt_provider import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_refresh_token,
)
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import get_db_session
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.schemas.auth import (
    RefreshTokenRequest,
    TokenResponse,
    UserCreateRequest,
    UserResponse,
)


router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    query = select(UserModel).where(
        or_(UserModel.username == payload.username, UserModel.email == payload.email)
    )
    existing = await db.execute(query)
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        )

    user = UserModel(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent registration with the same username/email can slip past
        # the pre-check above; surface it as a clean 409, not a 500.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from None
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.post("/token", response_model=TokenResponse)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    result = await db.execute(select(UserModel).where(UserModel.username == form_data.username))
    user = result.scalars().first()

    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    settings = get_settings()
    token = create_access_token(data={"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        refresh_token=create_refresh_token(data={"sub": str(user.id)}),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(
    payload: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    """Exchange a valid refresh token for a fresh access token."""
    user_id = verify_refresh_token(payload.refresh_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
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

    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(data={"sub": str(user.id)}),
        refresh_token=create_refresh_token(data={"sub": str(user.id)}),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(current_user)


