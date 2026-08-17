"""Application service for the user file system.

Orchestrates the file/folder repositories, object storage metadata
operations, and subscription-based size limits. Routers stay thin and the
business rules (ownership, size limits, recursive-delete coordination) are
unit-testable here.
"""

from pathlib import Path
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from src.application.dtos.upload_dto import UploadSession
from src.application.exceptions.file_system_exceptions import (
    FileRecordNotFoundError,
    FileSizeLimitExceededError,
    FolderNameConflictError,
    FolderNotFoundError,
)
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.models import UserFileModel, UserFolderModel


class FileRepositoryPort(Protocol):
    """Persists user file records."""

    async def save(
        self, *, user_id: int, file_key: str, file_name: str,
        file_size_bytes: int, mime_type: str, folder_id: str | None = None,
        expires_at=None,
    ) -> str: ...

    async def get_by_id(self, file_id: str) -> UserFileModel | None: ...

    async def list_by_user(
        self, user_id: int, *, folder_id: str | None = None,
        offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFileModel], int]: ...

    async def move(self, file_id: str, folder_id: str | None) -> bool: ...

    async def delete(self, file_id: str) -> bool: ...


class FolderRepositoryPort(Protocol):
    """Persists the user folder hierarchy."""

    async def create(
        self, *, user_id: int, name: str, parent_id: str | None = None,
    ) -> UserFolderModel: ...

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None: ...

    async def list_by_parent(
        self, user_id: int, parent_id: str | None, *, offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFolderModel], int]: ...

    async def list_files_in_folder(
        self, user_id: int, folder_id: str | None, *, offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFileModel], int]: ...

    async def rename(self, folder_id: str, name: str) -> bool: ...

    async def delete_with_descendants(
        self, folder_id: str,
    ) -> tuple[list[str], list[str]]: ...


class SubscriptionTierPort(Protocol):
    """Reads the actor's subscription tier."""

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier: ...


class FileStorageOperations(Protocol):
    """Minimal object-storage surface used by the file system."""

    async def stat_object(self, object_key: str) -> dict | None: ...

    async def remove_object(self, object_key: str) -> bool: ...


# Tier → max upload size (bytes). Values mirror the settings file-size limits.
def _default_size_limits() -> dict[SubscriptionTier, int]:
    settings = get_settings()
    return {
        SubscriptionTier.GUEST: settings.GUEST_MAX_FILE_SIZE,
        SubscriptionTier.FREE: settings.FREE_MAX_FILE_SIZE,
        SubscriptionTier.PREMIUM: settings.PRO_PLUS_MAX_FILE_SIZE,
    }


class FileService:
    """Owns the rules for folders, files and verified uploads."""

    def __init__(
        self,
        file_repository: FileRepositoryPort,
        folder_repository: FolderRepositoryPort,
        storage: FileStorageOperations,
        subscription_repository: SubscriptionTierPort,
        size_limits: dict[SubscriptionTier, int] | None = None,
    ):
        self._files = file_repository
        self._folders = folder_repository
        self._storage = storage
        self._subscriptions = subscription_repository
        self._size_limits = size_limits or _default_size_limits()

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    async def create_folder(
        self, user_id: int, name: str, parent_id: str | None = None,
    ) -> UserFolderModel:
        """Create a folder inside an owned parent (root when ``parent_id`` is None)."""
        if parent_id is not None:
            await self.get_folder(user_id, parent_id)
        try:
            return await self._folders.create(
                user_id=user_id, name=name.strip(), parent_id=parent_id,
            )
        except IntegrityError as exc:
            raise FolderNameConflictError() from exc

    async def get_folder(self, user_id: int, folder_id: str) -> UserFolderModel:
        """Fetch a folder owned by the user, else raise FolderNotFoundError."""
        folder = await self._folders.get_by_id(folder_id)
        if folder is None or folder.user_id != user_id:
            raise FolderNotFoundError()
        return folder

    async def list_root_folders(
        self, user_id: int, *, offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFolderModel], int]:
        return await self._folders.list_by_parent(user_id, parent_id=None, offset=offset, limit=limit)

    async def get_folder_contents(
        self, user_id: int, folder_id: str, *, offset: int = 0, limit: int = 50,
    ) -> tuple[UserFolderModel, list[UserFolderModel], int, list[UserFileModel], int]:
        """Return (folder, subfolders, total_folders, files, total_files)."""
        folder = await self.get_folder(user_id, folder_id)
        subfolders, total_folders = await self._folders.list_by_parent(
            user_id, parent_id=folder.id, offset=offset, limit=limit,
        )
        files, total_files = await self._folders.list_files_in_folder(
            user_id, folder_id=folder.id, offset=offset, limit=limit,
        )
        return folder, subfolders, total_folders, files, total_files

    async def rename_folder(self, user_id: int, folder_id: str, name: str) -> UserFolderModel:
        folder = await self.get_folder(user_id, folder_id)
        try:
            renamed = await self._folders.rename(folder.id, name.strip())
        except IntegrityError as exc:
            raise FolderNameConflictError() from exc
        if not renamed:
            raise FolderNotFoundError()
        updated = await self._folders.get_by_id(folder.id)
        assert updated is not None
        return updated

    async def delete_folder(self, user_id: int, folder_id: str) -> None:
        """Delete a folder recursively — DB rows and object-storage keys."""
        folder = await self.get_folder(user_id, folder_id)
        file_keys, _ = await self._folders.delete_with_descendants(folder.id)
        for key in file_keys:
            await self._storage.remove_object(key)

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        """Fetch a file owned by the user, else raise FileRecordNotFoundError."""
        row = await self._files.get_by_id(file_id)
        if row is None or row.user_id != user_id:
            raise FileRecordNotFoundError()
        return row

    async def list_files(
        self, user_id: int, folder_id: str | None = None, *, offset: int = 0, limit: int = 20,
    ) -> tuple[list[UserFileModel], int]:
        """List files inside a folder (or root). Validates folder ownership."""
        if folder_id is not None:
            await self.get_folder(user_id, folder_id)
        return await self._files.list_by_user(
            user_id, folder_id=folder_id, offset=offset, limit=limit,
        )

    async def move_file(
        self, user_id: int, file_id: str, folder_id: str | None,
    ) -> UserFileModel:
        """Move a file into a folder (or to root when ``folder_id`` is None)."""
        row = await self.get_file(user_id, file_id)
        if folder_id is not None:
            await self.get_folder(user_id, folder_id)
        if not await self._files.move(row.id, folder_id):
            raise FileRecordNotFoundError()
        updated = await self._files.get_by_id(row.id)
        assert updated is not None
        return updated

    async def delete_file(self, user_id: int, file_id: str) -> None:
        """Delete a file: object first, then the DB record."""
        row = await self.get_file(user_id, file_id)
        await self._storage.remove_object(row.file_key)
        await self._files.delete(row.id)

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------

    async def complete_upload(self, user_id: int, session: UploadSession) -> str:
        """
        Finalize a verified upload: enforce the tier size limit, validate the
        target folder, and persist the file record. Returns the new file id.
        """
        stats = await self._storage.stat_object(session.object_key) or {}
        size = int(stats.get("size", 0))

        tier = await self._subscriptions.get_tier_for_user(user_id)
        max_size = self._size_limits[tier]
        if size > max_size:
            raise FileSizeLimitExceededError(
                f"File exceeds the maximum size for your tier ({max_size // (1024 * 1024)} MB)."
            )

        if session.folder_id is not None:
            await self.get_folder(user_id, session.folder_id)

        file_name = session.file_name or Path(session.object_key).name
        return await self._files.save(
            user_id=user_id,
            file_key=session.object_key,
            file_name=file_name,
            file_size_bytes=size,
            mime_type=stats.get("content_type") or "application/octet-stream",
            folder_id=session.folder_id,
        )
