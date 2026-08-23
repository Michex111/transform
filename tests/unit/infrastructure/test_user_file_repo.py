"""Tests for the user file repository (favorite support)."""

import asyncio
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.database.models import UserFileModel, UserModel
from src.infrastructure.database.session import Base


@contextmanager
def sqlite_session_factory():
    """Yields an async_sessionmaker bound to a fresh in-memory SQLite DB."""

    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

    engine, factory = asyncio.run(_setup())
    try:
        yield factory
    finally:
        asyncio.run(engine.dispose())


async def _add_user(factory, username: str = "file-user") -> UserModel:
    async with factory() as session:
        user = UserModel(
            username=username, email=f"{username}@example.com", hashed_password="x", is_active=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _add_file(factory, *, user_id: int, file_key: str, folder_id: str | None = None) -> str:
    async with factory() as session:
        return await SQLUserFileRepository(session).save(
            user_id=user_id,
            file_key=file_key,
            file_name=file_key.split("/")[-1],
            file_size_bytes=10,
            mime_type="application/pdf",
            folder_id=folder_id,
        )


def test_set_favorite_flag() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                file_id = await _add_file(factory, user_id=user.id, file_key="a.pdf")
                repo = SQLUserFileRepository(session)

                assert await repo.set_favorite(file_id, True) is True
                row = await repo.get_by_id(file_id)
                assert row is not None
                assert row.is_favorite is True

                assert await repo.set_favorite(file_id, False) is True
                row = await repo.get_by_id(file_id)
                assert row is not None
                assert row.is_favorite is False

        asyncio.run(_run())


def test_set_favorite_unknown_file_returns_false() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                repo = SQLUserFileRepository(session)
                assert await repo.set_favorite("missing", True) is False

        asyncio.run(_run())


def test_list_favorites_filters_and_paginates() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                other = await _add_user(factory, username="file-user-2")
                repo = SQLUserFileRepository(session)

                fav_a = await _add_file(factory, user_id=user.id, file_key="a.pdf")
                fav_b = await _add_file(factory, user_id=user.id, file_key="b.pdf")
                non_fav = await _add_file(factory, user_id=user.id, file_key="c.pdf")
                other_fav = await _add_file(factory, user_id=other.id, file_key="d.pdf")

                await repo.set_favorite(fav_a, True)
                await repo.set_favorite(fav_b, True)
                await repo.set_favorite(other_fav, True)

                rows, total = await repo.list_favorites(user.id, offset=0, limit=50)
                assert total == 2
                assert {r.id for r in rows} == {fav_a, fav_b}
                assert non_fav not in {r.id for r in rows}

                # pagination
                rows, total = await repo.list_favorites(user.id, offset=0, limit=1)
                assert total == 2
                assert len(rows) == 1

        asyncio.run(_run())


def test_list_favorites_empty() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                repo = SQLUserFileRepository(session)
                rows, total = await repo.list_favorites(user.id)
                assert total == 0
                assert rows == []

        asyncio.run(_run())
