"""SQLAlchemy repository for user files stored in S3/Minio."""

import uuid
from datetime import datetime, UTC

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import UserFileModel


class SQLUserFileRepository:
    """Persists and queries user file metadata in PostgreSQL."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, *, user_id: int, file_key: str, file_name: str,
                   file_size_bytes: int, mime_type: str,
                   folder_id: str | None = None,
                   expires_at: datetime | None = None) -> str:
        """Create a new file record. Returns the new file ID."""
        file_id = str(uuid.uuid4())
        record = UserFileModel(
            id=file_id,
            user_id=user_id,
            folder_id=folder_id,
            file_key=file_key,
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            mime_type=mime_type,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.commit()
        return file_id

    async def get_by_id(self, file_id: str) -> UserFileModel | None:
        """Fetch a single file record by primary key."""
        result = await self._session.execute(
            select(UserFileModel).where(UserFileModel.id == file_id)
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self, user_id: int, *, folder_id: str | None = None,
        offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFileModel], int]:
        """
        Return a paginated list of files for a user, newest first.

        When ``folder_id`` is provided only files directly inside that folder
        are returned; when omitted, only root-level files (``folder_id IS
        NULL``) are returned.

        Returns:
            (rows, total_count)
        """
        base = select(UserFileModel).where(
            UserFileModel.user_id == user_id,
            UserFileModel.folder_id == folder_id,
        )

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar_one()

        rows_q = base.order_by(UserFileModel.created_at.desc()).offset(offset).limit(limit)
        rows = (await self._session.execute(rows_q)).scalars().all()

        return list(rows), total

    async def move(self, file_id: str, folder_id: str | None) -> bool:
        """Move a file into a folder (or to root when ``folder_id`` is None)."""
        result = await self._session.execute(
            update(UserFileModel)
            .where(UserFileModel.id == file_id)
            .values(folder_id=folder_id)
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def rename(self, file_id: str, file_name: str) -> bool:
        """Update a file's display name. Returns True if a row was updated."""
        result = await self._session.execute(
            update(UserFileModel)
            .where(UserFileModel.id == file_id)
            .values(file_name=file_name)
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def set_favorite(self, file_id: str, is_favorite: bool) -> bool:
        """Set (or clear) the favorite flag for a file. Returns True if a row
        was updated."""
        result = await self._session.execute(
            update(UserFileModel)
            .where(UserFileModel.id == file_id)
            .values(is_favorite=is_favorite)
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def list_favorites(
        self, user_id: int, *, offset: int = 0, limit: int = 50,
    ) -> tuple[list[UserFileModel], int]:
        """
        Return a paginated list of the user's favorite files, newest first.

        Returns:
            (rows, total_count)
        """
        base = select(UserFileModel).where(
            UserFileModel.user_id == user_id,
            UserFileModel.is_favorite.is_(True),
        )

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self._session.execute(count_q)).scalar_one()

        rows_q = base.order_by(UserFileModel.created_at.desc()).offset(offset).limit(limit)
        rows = (await self._session.execute(rows_q)).scalars().all()

        return list(rows), total

    async def delete(self, file_id: str) -> bool:
        """Delete a file record by ID. Returns True if a row was removed."""
        record = await self.get_by_id(file_id)
        if record is None:
            return False
        await self._session.delete(record)
        await self._session.commit()
        return True

    async def count_user_files(self, user_id: int) -> int:
        """Total number of file records owned by a user (all folders)."""
        result = await self._session.execute(
            select(func.count()).select_from(UserFileModel).where(
                UserFileModel.user_id == user_id
            )
        )
        return result.scalar_one()

    async def get_user_storage_used(self, user_id: int) -> int:
        """Sum of file_size_bytes for all files owned by a user."""
        result = await self._session.execute(
            select(func.coalesce(func.sum(UserFileModel.file_size_bytes), 0))
            .where(UserFileModel.user_id == user_id)
        )
        return result.scalar_one()
