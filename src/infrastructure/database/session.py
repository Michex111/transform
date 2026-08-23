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
    url = resolve_database_url()
    is_pooler = "pooler" in url or "neon.tech" in url
    pool_kwargs: dict = {
        "echo": os.getenv("SQL_ECHO", "0") == "1",
        "pool_pre_ping": True,
    }
    if is_pooler:
        # Neon (and PgBouncer-style) poolers close idle connections and reject
        # long-lived prepared statements. Recycle connections ahead of the
        # pooler's idle timeout and disable the asyncpg prepared-statement
        # cache so a recycled connection never fails with a stale
        # "connection is closed" / "prepared statement already exists" error.
        pool_kwargs.update({
            "pool_recycle": 300,  # recycle every 5 min (Neon idle timeout ~ few min)
            "pool_timeout": 30,
            "connect_args": {"prepared_statement_cache_size": 0},
        })
    else:
        pool_kwargs["pool_recycle"] = 1800
    return create_async_engine(url, **pool_kwargs)


@lru_cache
def get_session_factory():
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session
