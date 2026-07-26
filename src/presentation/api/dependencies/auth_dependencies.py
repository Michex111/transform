from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.jwt_provider import verify_access_token
from src.infrastructure.database.models import UserModel
from src.infrastructure.database.session import get_db_session


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/web/users/token")


async def get_current_user(
	token: Annotated[str, Depends(oauth2_scheme)],
	db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserModel:
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


CurrentUser = Annotated[UserModel, Depends(get_current_user)]