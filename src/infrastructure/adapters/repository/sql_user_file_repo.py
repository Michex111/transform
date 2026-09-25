"""SQLAlchemy repository for user files stored in S3/Minio."""

import uuid
from dataclasses import dataclass
from datetime import datetime, UTC

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.adapters.storage.sanitize import normalize_extension
from src.infrastructure.database.models import UserFileModel, UserModel


@dataclass(frozen=True)
class StorageBreakdownRow:
    """One row of the per-extension storage aggregation."""

    extension: str
    bytes: int
    file_count: int


class SQLUserFileRepository:
    """Persists and queries user file metadata in PostgreSQL."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def save(self, *, user_id: int, file_key: str, file_name: str,
                   file_size_bytes: int, mime_type: str,
                   file_extension: str = "",
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
            file_extension=normalize_extension(file_extension),
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

    async def find_by_key(self, user_id: int, file_key: str) -> UserFileModel | None:
        """Fetch a user's file record for a given object key, if any.

        Used to keep upload verification idempotent: the same object key must
        not produce two library rows.
        """
        result = await self._session.execute(
            select(UserFileModel).where(
                UserFileModel.user_id == user_id,
                UserFileModel.file_key == file_key,
            )
        )
        return result.scalars().first()

    async def list_owned_keys(self, user_id: int, file_keys: list[str]) -> set[str]:
        """Return the subset of ``file_keys`` that belong to ``user_id``.

        One ``IN`` query instead of a probe per key; used to authorise
        presigned-URL requests without leaking other tenants' objects.
        """
        unique_keys = list(dict.fromkeys(file_keys))
        if not unique_keys:
            return set()
        result = await self._session.execute(
            select(UserFileModel.file_key).where(
                UserFileModel.user_id == user_id,
                UserFileModel.file_key.in_(unique_keys),
            )
        )
        return set(result.scalars().all())

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
        result = await self._session.execute(
            delete(UserFileModel).where(UserFileModel.id == file_id)
        )
        await self._session.commit()
        return result.rowcount > 0  # type: ignore[attr-defined]

    async def delete_many(self, user_id: int, file_ids: list[str]) -> list[UserFileModel]:
        """Delete several file records owned by ``user_id`` in one statement.

        Returns the rows that were actually deleted. Unknown or foreign ids are
        silently skipped (never an error).
        """
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids:
            return []
        result = await self._session.execute(
            select(UserFileModel).where(
                UserFileModel.user_id == user_id,
                UserFileModel.id.in_(unique_ids),
            )
        )
        rows = list(result.scalars().all())
        if not rows:
            return []
        await self._session.execute(
            delete(UserFileModel).where(
                UserFileModel.user_id == user_id,
                UserFileModel.id.in_([row.id for row in rows]),
            )
        )
        await self._session.commit()
        return rows

    async def count_user_files(self, user_id: int) -> int:
        """Total number of file records owned by a user (all folders)."""
        result = await self._session.execute(
            select(func.count()).select_from(UserFileModel).where(
                UserFileModel.user_id == user_id
            )
        )
        return result.scalar_one()

    async def get_user_storage_used(self, user_id: int) -> int:
        """Sum of ``file_size_bytes`` for all files owned by a user.

        The result is coerced to ``int``, and that is load-bearing rather than
        cosmetic. ``SUM()`` over a ``BIGINT`` column comes back from
        **PostgreSQL as a ``Decimal``** (asyncpg maps NUMERIC-like results that
        way) while SQLite — used by the test suite — returns a plain ``int``.
        Leaving it alone therefore worked in every test and broke in
        production: the value is embedded in the structured 413 body for an
        over-quota upload, and FastAPI's JSON encoder cannot serialise a
        ``Decimal``, so the refusal raised and surfaced as a **500** instead of
        "not enough storage". Converting here keeps the declared return type
        true for every backend, so no caller has to know which one it is on.
        """
        result = await self._session.execute(
            select(func.coalesce(func.sum(UserFileModel.file_size_bytes), 0))
            .where(UserFileModel.user_id == user_id)
        )
        return int(result.scalar_one())

    async def lock_user_for_update(self, user_id: int) -> None:
        """Take a row lock on the user, serialising storage commits for them.

        The lock is on the ``users`` row rather than on ``user_files`` because
        the aggregate being protected is "this user's total storage": there is
        no single file row to lock *before* the new row exists, and locking the
        whole table would serialise unrelated users.

        It lives on this repository because this repository owns
        ``get_user_storage_used`` — the number the quota check reads — and the
        lock is only meaningful when it is held across that read and the
        subsequent insert. Callers must therefore run both in the same session
        (they do: one request, one injected ``AsyncSession``).

        SQLite ignores ``FOR UPDATE`` (and serialises writes anyway), so the
        test suite exercises the same code path without the lock. PostgreSQL
        honours it.
        """
        await self._session.execute(
            select(UserModel.id).where(UserModel.id == user_id).with_for_update()
        )

    async def get_storage_breakdown_by_extension(
        self, user_id: int
    ) -> list[StorageBreakdownRow]:
        """Storage usage per file extension for a user, largest first.

        Runs a single ``GROUP BY file_extension`` aggregation. Zero-byte files
        are excluded so the result only contains entries with ``bytes > 0``.
        """
        total_bytes = func.sum(UserFileModel.file_size_bytes)
        result = await self._session.execute(
            select(
                UserFileModel.file_extension,
                total_bytes,
                func.count(),
            )
            .where(
                UserFileModel.user_id == user_id,
                UserFileModel.file_size_bytes > 0,
            )
            .group_by(UserFileModel.file_extension)
            .order_by(total_bytes.desc())
        )
        return [
            StorageBreakdownRow(
                extension=extension,
                bytes=int(group_bytes),
                file_count=int(group_count),
            )
            for extension, group_bytes, group_count in result.all()
        ]
