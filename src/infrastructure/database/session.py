import os
from functools import lru_cache
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infrastructure.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def resolve_database_url() -> str:
    env_database_url = os.getenv("DATABASE_URL")
    database_url = env_database_url or get_settings().DATABASE_URL.get_secret_value()
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


@lru_cache
def get_engine():
    return create_async_engine(resolve_database_url(), echo=True)


@lru_cache
def get_session_factory():
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session