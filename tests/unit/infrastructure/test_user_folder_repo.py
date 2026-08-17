"""Tests for the user folder repository and folder-aware file repository."""

import asyncio
from contextlib import contextmanager
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFileModel, UserFolderModel, UserModel
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


async def _add_user(factory, username: str = "folder-user") -> UserModel:
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


def test_folder_create_and_fetch() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                repo = SQLUserFolderRepository(session)

                folder = await repo.create(user_id=user.id, name="Documents")
                assert folder.id
                assert folder.parent_id is None

                fetched = await repo.get_by_id(folder.id)
                assert fetched is not None
                assert fetched.name == "Documents"
                assert fetched.user_id == user.id

        asyncio.run(_run())


def test_nested_folder_create() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                repo = SQLUserFolderRepository(session)

                root = await repo.create(user_id=user.id, name="Root")
                child = await repo.create(user_id=user.id, name="Child", parent_id=root.id)

                assert child.parent_id == root.id

                children, total = await repo.list_by_parent(user.id, parent_id=root.id)
                assert total == 1
                assert children[0].id == child.id

        asyncio.run(_run())


def test_same_name_in_same_parent_rejected() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                repo = SQLUserFolderRepository(session)
                parent = await repo.create(user_id=user.id, name="Parent")
                await repo.create(user_id=user.id, name="Docs", parent_id=parent.id)
                try:
                    await repo.create(user_id=user.id, name="Docs", parent_id=parent.id)
                    raise AssertionError("expected IntegrityError")
                except IntegrityError:
                    await session.rollback()

        asyncio.run(_run())


def test_folder_list_isolated_per_user() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user_a = await _add_user(factory, username="folder-user-a")
                user_b = await _add_user(factory, username="folder-user-b")
                repo = SQLUserFolderRepository(session)
                await repo.create(user_id=user_a.id, name="A's folder")
                await repo.create(user_id=user_b.id, name="B's folder")

                rows, total = await repo.list_by_parent(user_a.id, parent_id=None)
                assert total == 1
                assert rows[0].name == "A's folder"

        asyncio.run(_run())


def test_folder_rename() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                repo = SQLUserFolderRepository(session)
                folder = await repo.create(user_id=user.id, name="Old")

                assert await repo.rename(folder.id, "New") is True
                renamed = await repo.get_by_id(folder.id)
                assert renamed is not None
                assert renamed.name == "New"

        asyncio.run(_run())


def test_delete_with_descendants_removes_subtree_and_files() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                folder_repo = SQLUserFolderRepository(session)

                root = await folder_repo.create(user_id=user.id, name="Root")
                sub = await folder_repo.create(user_id=user.id, name="Sub", parent_id=root.id)
                deep = await folder_repo.create(user_id=user.id, name="Deep", parent_id=sub.id)

                # Files in root, sub, and outside the tree
                await _add_file(factory, user_id=user.id, file_key="root-file.pdf", folder_id=root.id)
                await _add_file(factory, user_id=user.id, file_key="sub-file.pdf", folder_id=sub.id)
                await _add_file(factory, user_id=user.id, file_key="root-level.pdf", folder_id=None)

                file_keys, folder_ids = await folder_repo.delete_with_descendants(root.id)

                assert set(folder_ids) == {root.id, sub.id, deep.id}
                assert set(file_keys) == {"root-file.pdf", "sub-file.pdf"}

                async with factory() as session:
                    assert await session.get(UserFolderModel, root.id) is None
                    assert await session.get(UserFolderModel, sub.id) is None
                    assert await session.get(UserFolderModel, deep.id) is None
                    # Files outside the tree survive
                    row = (await session.execute(
                        select(UserFileModel).where(UserFileModel.file_key == "root-level.pdf")
                    )).scalar_one()
                    assert row is not None

        asyncio.run(_run())


def test_delete_nonexistent_folder_returns_empty() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                repo = SQLUserFolderRepository(session)
                file_keys, folder_ids = await repo.delete_with_descendants("missing")
                assert file_keys == []
                assert folder_ids == ["missing"]

        asyncio.run(_run())


def test_file_repo_folder_filter_and_move() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                folder_repo = SQLUserFolderRepository(session)
                file_repo = SQLUserFileRepository(session)

                folder = await folder_repo.create(user_id=user.id, name="Docs")
                await _add_file(factory, user_id=user.id, file_key="a.pdf", folder_id=folder.id)
                root_file_id = await _add_file(factory, user_id=user.id, file_key="b.pdf", folder_id=None)

                rows, total = await file_repo.list_by_user(user.id, folder_id=folder.id)
                assert total == 1
                assert rows[0].file_key == "a.pdf"

                # root-only listing
                rows, total = await file_repo.list_by_user(user.id, folder_id=None)
                assert total == 1
                assert rows[0].file_key == "b.pdf"

                # move the root file into the folder
                assert await file_repo.move(root_file_id, folder.id) is True
                rows, total = await file_repo.list_by_user(user.id, folder_id=folder.id)
                assert total == 2

                # move it back to root
                assert await file_repo.move(root_file_id, None) is True
                rows, total = await file_repo.list_by_user(user.id, folder_id=None)
                assert total == 1
                assert rows[0].file_key == "b.pdf"

                # the in-folder file is untouched
                rows, total = await file_repo.list_by_user(user.id, folder_id=folder.id)
                assert total == 1
                assert rows[0].file_key == "a.pdf"

        asyncio.run(_run())


def test_count_user_files_counts_all_folders() -> None:
    with sqlite_session_factory() as factory:

        async def _run() -> None:
            async with factory() as session:
                user = await _add_user(factory)
                folder_repo = SQLUserFolderRepository(session)
                file_repo = SQLUserFileRepository(session)

                folder = await folder_repo.create(user_id=user.id, name="Docs")
                await _add_file(factory, user_id=user.id, file_key="a.pdf", folder_id=folder.id)
                await _add_file(factory, user_id=user.id, file_key="b.pdf", folder_id=None)

                assert await file_repo.count_user_files(user.id) == 2

        asyncio.run(_run())
