import os
from functools import lru_cache
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

from src.infrastructure.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(database_url: str) -> str:
    url = make_url(database_url)

    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")

    if url.drivername == "postgresql+asyncpg":
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode and "ssl" not in query:
            query["ssl"] = sslmode
        url = url.set(query=query)

    return url.render_as_string(hide_password=False)


def resolve_database_url() -> str:
    env_database_url = os.getenv("DATABASE_URL")
    database_url = env_database_url or get_settings().DATABASE_URL.get_secret_value()
    return normalize_database_url(database_url)


@lru_cache
def get_engine():
    # echo=False: never log query parameters (PII) in production; enable
    # explicitly with SQL_ECHO=1 when debugging locally.
    return create_async_engine(
        resolve_database_url(),
        echo=os.getenv("SQL_ECHO", "0") == "1",
    )


@lru_cache
def get_session_factory():
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session
