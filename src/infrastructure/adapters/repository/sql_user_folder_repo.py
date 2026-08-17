"""SQLAlchemy repository for user folders."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import UserFileModel, UserFolderModel


class SQLUserFolderRepository:
    """Persists and queries the user folder hierarchy."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(self, *, user_id: int, name: str, parent_id: str | None = None) -> UserFolderModel:
        """Create a folder. Returns the created row."""
        folder = UserFolderModel(
            id=str(uuid.uuid4()),
            user_id=user_id,
            parent_id=parent_id,
            name=name,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(folder)
        await self._session.commit()
        await self._session.refresh(folder)
        return folder

    async def rename(self, folder_id: str, name: str) -> bool:
        """Rename a folder. Returns True when a row was updated."""
        result = await self._session.execute(
            update(UserFolderModel)
            .where(UserFolderModel.id == folder_id)
            .values(name=name, updated_at=datetime.now(UTC))
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def delete_with_descendants(
        self, folder_id: str
    ) -> tuple[list[str], list[str]]:
        """
        Delete a folder and all of its descendants (folders + files).

        Returns:
            (deleted_file_keys, deleted_folder_ids). Object-storage cleanup is
            the caller's responsibility using the returned file keys.
        """
        # Breadth-first walk to collect the folder subtree.
        folder_ids: list[str] = []
        frontier = [folder_id]
        while frontier:
            result = await self._session.execute(
                select(UserFolderModel.id).where(UserFolderModel.parent_id.in_(frontier))
            )
            children = [row[0] for row in result.all()]
            folder_ids.extend(children)
            frontier = children

        all_ids = [folder_id, *folder_ids]

        # Collect file keys before deleting records.
        files_result = await self._session.execute(
            select(UserFileModel.file_key).where(UserFileModel.folder_id.in_(all_ids))
        )
        file_keys = [row[0] for row in files_result.all()]

        if file_keys:
            await self._session.execute(
                delete(UserFileModel).where(UserFileModel.folder_id.in_(all_ids))
            )
        if all_ids:
            await self._session.execute(
                delete(UserFolderModel).where(UserFolderModel.id.in_(all_ids))
            )
        await self._session.commit()
        return file_keys, all_ids

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None:
        """Fetch a folder row by primary key."""
        return await self._session.get(UserFolderModel, folder_id)

    async def list_by_parent(
        self, user_id: int, parent_id: str | None, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[UserFolderModel], int]:
        """
        List folders directly inside a parent folder (or root when
        ``parent_id`` is None), ordered by name.

        Returns:
            (rows, total_count)
        """
        base = select(UserFolderModel).where(
            UserFolderModel.user_id == user_id,
            UserFolderModel.parent_id == parent_id,
        )

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar_one()

        rows_q = (
            base.order_by(UserFolderModel.name.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(rows_q)).scalars().all()
        return list(rows), total

    async def list_files_in_folder(
        self, user_id: int, folder_id: str | None, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[UserFileModel], int]:
        """
        List files directly inside a folder (or root when ``folder_id`` is
        None), newest first.

        Returns:
            (rows, total_count)
        """
        base = select(UserFileModel).where(
            UserFileModel.user_id == user_id,
            UserFileModel.folder_id == folder_id,
        )

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar_one()

        rows_q = (
            base.order_by(UserFileModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(rows_q)).scalars().all()
        return list(rows), total
