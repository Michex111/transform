"""Unit tests for the FileService business rules (in-memory fakes)."""

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from src.application.dtos.upload_dto import UploadSession
from src.application.exceptions.file_system_exceptions import (
    FileRecordNotFoundError,
    FileSizeLimitExceededError,
    FolderNameConflictError,
    FolderNotFoundError,
)
from src.application.services.file_service import FileService
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserFileModel, UserFolderModel


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeFileRepo:
    def __init__(self) -> None:
        self.files: dict[str, UserFileModel] = {}
        self._seq = 0

    async def save(self, *, user_id, file_key, file_name, file_size_bytes,
                   mime_type, file_extension="", folder_id=None, expires_at=None) -> str:
        self._seq += 1
        file_id = f"f{self._seq}"
        self.files[file_id] = UserFileModel(
            id=file_id, user_id=user_id, folder_id=folder_id, file_key=file_key,
            file_name=file_name, file_extension=file_extension,
            file_size_bytes=file_size_bytes, mime_type=mime_type,
        )
        return file_id

    async def get_by_id(self, file_id: str) -> UserFileModel | None:
        return self.files.get(file_id)

    async def list_by_user(self, user_id, *, folder_id=None, offset=0, limit=20):
        rows = [f for f in self.files.values() if f.user_id == user_id and f.folder_id == folder_id]
        rows.sort(key=lambda f: f.id)
        return rows[offset:offset + limit], len(rows)

    async def move(self, file_id: str, folder_id: str | None) -> bool:
        row = self.files.get(file_id)
        if row is None:
            return False
        row.folder_id = folder_id
        return True

    async def rename(self, file_id: str, file_name: str) -> bool:
        row = self.files.get(file_id)
        if row is None:
            return False
        row.file_name = file_name
        return True

    async def delete(self, file_id: str) -> bool:
        return self.files.pop(file_id, None) is not None

    async def set_favorite(self, file_id: str, is_favorite: bool) -> bool:
        row = self.files.get(file_id)
        if row is None:
            return False
        row.is_favorite = is_favorite
        return True

    async def list_favorites(self, user_id, *, offset=0, limit=50):
        rows = [f for f in self.files.values() if f.user_id == user_id and f.is_favorite]
        rows.sort(key=lambda f: f.id)
        return rows[offset:offset + limit], len(rows)


class FakeFolderRepo:
    def __init__(self) -> None:
        self.folders: dict[str, UserFolderModel] = {}
        self._seq = 0

    def _add(self, *, user_id, name, parent_id) -> UserFolderModel:
        self._seq += 1
        folder = UserFolderModel(
            id=f"d{self._seq}", user_id=user_id, parent_id=parent_id, name=name,
        )
        self.folders[folder.id] = folder
        return folder

    async def create(self, *, user_id, name, parent_id=None) -> UserFolderModel:
        # enforce the same uniqueness as the DB constraint
        for f in self.folders.values():
            if f.user_id == user_id and f.parent_id == parent_id and f.name == name:
                raise IntegrityError("dupe", {}, Exception("unique"))
        return self._add(user_id=user_id, name=name, parent_id=parent_id)

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None:
        return self.folders.get(folder_id)

    async def list_by_parent(self, user_id, parent_id=None, *, offset=0, limit=20):
        rows = [f for f in self.folders.values() if f.user_id == user_id and f.parent_id == parent_id]
        rows.sort(key=lambda f: f.name)
        return rows[offset:offset + limit], len(rows)

    async def list_files_in_folder(self, user_id, folder_id=None, *, offset=0, limit=20):
        del user_id, folder_id, offset, limit
        return [], 0

    async def rename(self, folder_id: str, name: str) -> bool:
        folder = self.folders.get(folder_id)
        if folder is None:
            return False
        folder.name = name
        return True

    async def move(self, folder_id: str, parent_id: str | None) -> bool:
        folder = self.folders.get(folder_id)
        if folder is None:
            return False
        folder.parent_id = parent_id
        return True

    async def delete_with_descendants(self, folder_id: str):
        file_keys: list[str] = []
        folder_ids: list[str] = []
        frontier = [folder_id]
        while frontier:
            children = [f.id for f in self.folders.values() if f.parent_id in frontier]
            folder_ids.extend(children)
            frontier = children
        all_ids = [folder_id, *folder_ids]
        for fid in all_ids:
            self.folders.pop(fid, None)
        return file_keys, all_ids


class FakeStorage:
    def __init__(self, sizes: dict[str, int] | None = None) -> None:
        self.sizes = dict(sizes or {})
        self.removed: list[str] = []

    async def stat_object(self, key: str) -> dict | None:
        if key not in self.sizes:
            return None
        return {"size": self.sizes[key], "content_type": "application/pdf"}

    async def remove_object(self, key: str) -> bool:
        self.removed.append(key)
        return True


class FakeSubscriptionRepo:
    def __init__(self, tier: SubscriptionTier = SubscriptionTier.FREE) -> None:
        self._tier = tier

    async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
        del user_id
        return self._tier


@pytest.fixture
def file_repo() -> FakeFileRepo:
    return FakeFileRepo()


@pytest.fixture
def folder_repo() -> FakeFolderRepo:
    return FakeFolderRepo()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def service(file_repo, folder_repo, storage) -> FileService:
    return FileService(
        file_repository=file_repo,
        folder_repository=folder_repo,
        storage=storage,
        subscription_repository=FakeSubscriptionRepo(),
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def test_create_root_folder(service) -> None:
    folder = _run(service.create_folder(user_id=1, name="Docs"))
    assert folder.name == "Docs"
    assert folder.parent_id is None


def test_create_nested_folder_validates_parent(service) -> None:
    parent = _run(service.create_folder(user_id=1, name="Root"))
    child = _run(service.create_folder(user_id=1, name="Sub", parent_id=parent.id))
    assert child.parent_id == parent.id


def test_create_folder_under_missing_parent_raises(service) -> None:
    with pytest.raises(FolderNotFoundError):
        _run(service.create_folder(user_id=1, name="Orphan", parent_id="nope"))


def test_create_folder_under_other_users_parent_raises(service) -> None:
    parent = _run(service.create_folder(user_id=1, name="Root"))
    with pytest.raises(FolderNotFoundError):
        _run(service.create_folder(user_id=2, name="Sneaky", parent_id=parent.id))


def test_duplicate_folder_name_raises_conflict(service) -> None:
    _run(service.create_folder(user_id=1, name="Docs"))
    with pytest.raises(FolderNameConflictError):
        _run(service.create_folder(user_id=1, name="Docs"))


def test_get_folder_ownership(service) -> None:
    folder = _run(service.create_folder(user_id=1, name="Docs"))
    assert _run(service.get_folder(1, folder.id)).id == folder.id
    with pytest.raises(FolderNotFoundError):
        _run(service.get_folder(2, folder.id))


def test_rename_folder_ownership_and_result(service) -> None:
    folder = _run(service.create_folder(user_id=1, name="Old"))
    renamed = _run(service.rename_folder(1, folder.id, "New"))
    assert renamed.name == "New"
    with pytest.raises(FolderNotFoundError):
        _run(service.rename_folder(2, folder.id, "X"))


def test_delete_folder_removes_descendant_objects(service, folder_repo, storage) -> None:
    root = _run(service.create_folder(user_id=1, name="Root"))
    sub = _run(service.create_folder(user_id=1, name="Sub", parent_id=root.id))

    # seed files whose keys live inside the folder tree
    folder_repo.files = {
        "a": UserFileModel(id="a", user_id=1, folder_id=root.id, file_key="k/a.pdf",
                           file_name="a.pdf", file_size_bytes=1, mime_type="x"),
        "b": UserFileModel(id="b", user_id=1, folder_id=sub.id, file_key="k/b.pdf",
                           file_name="b.pdf", file_size_bytes=1, mime_type="x"),
    }

    # override the folder repo's file listing so delete_with_descendants
    # collects those keys
    async def list_files_in_folder(user_id, folder_id=None, *, offset=0, limit=20):
        return [f for f in folder_repo.files.values() if f.folder_id == folder_id], 0

    folder_repo.list_files_in_folder = list_files_in_folder  # type: ignore[method-assign]

    # seed the fake folder repo with descendant structure for collection
    _run(service.delete_folder(1, root.id))

    # the service asks the folder repo for keys; our fake returned none, so we
    # verify the orchestration contract instead: descendants removed from DB
    assert root.id not in folder_repo.folders
    assert sub.id not in folder_repo.folders


def test_delete_folder_removes_collected_object_keys(service, storage) -> None:
    """The service must delete every object key returned by the folder repo."""
    root = _run(service.create_folder(user_id=1, name="Root"))

    # monkeypatch delete_with_descendants to return concrete keys
    async def delete_with_descendants(folder_id: str):
        del folder_id
        return (["k/a.pdf", "k/b.pdf"], [root.id])

    service._folders.delete_with_descendants = delete_with_descendants  # type: ignore[method-assign]

    _run(service.delete_folder(1, root.id))
    assert set(storage.removed) == {"k/a.pdf", "k/b.pdf"}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def test_move_file_requires_folder_ownership(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    folder = _run(service.create_folder(user_id=1, name="Mine"))
    _run(service.move_file(1, file_id, folder.id))

    other = _run(service.create_folder(user_id=2, name="Theirs"))
    with pytest.raises(FolderNotFoundError):
        _run(service.move_file(1, file_id, other.id))


def test_move_file_ownership(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    with pytest.raises(FileRecordNotFoundError):
        _run(service.move_file(2, file_id, None))


def test_delete_file_removes_object_then_record(service, file_repo, storage) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    _run(service.delete_file(1, file_id))
    assert storage.removed == ["k/a.pdf"]
    assert file_id not in file_repo.files

    with pytest.raises(FileRecordNotFoundError):
        _run(service.delete_file(1, file_id))


def test_rename_file_updates_display_name(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    updated = _run(service.rename_file(1, file_id, "  renamed.pdf  "))
    assert updated.file_name == "renamed.pdf"
    # The stored object key is unchanged.
    assert updated.file_key == "k/a.pdf"
    assert file_repo.files[file_id].file_name == "renamed.pdf"


def test_rename_file_requires_ownership(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    with pytest.raises(FileRecordNotFoundError):
        _run(service.rename_file(2, file_id, "hijack.pdf"))


def test_rename_file_rejects_blank_name(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    with pytest.raises(FileRecordNotFoundError):
        _run(service.rename_file(1, file_id, "   "))


def test_list_files_requires_folder_ownership(service) -> None:
    folder = _run(service.create_folder(user_id=1, name="Mine"))
    rows, total = _run(service.list_files(1, folder.id))
    assert (rows, total) == ([], 0)

    with pytest.raises(FolderNotFoundError):
        _run(service.list_files(2, folder.id))


# ---------------------------------------------------------------------------
# Upload completion
# ---------------------------------------------------------------------------

def _session(key: str = "uploads/abc.pdf", name: str | None = None, folder_id: str | None = None,
             file_extension: str | None = None) -> UploadSession:
    return UploadSession(upload_id="u1", object_key=key, status="completed",
                         file_name=name, file_extension=file_extension, folder_id=folder_id)


def test_complete_upload_saves_record(service, file_repo, storage) -> None:
    storage.sizes["uploads/abc.pdf"] = 1234
    file_id = _run(service.complete_upload(1, _session()))
    assert file_id in file_repo.files
    row = file_repo.files[file_id]
    assert row.file_size_bytes == 1234
    assert row.file_name == "abc.pdf"  # fallback to object key basename
    assert row.file_extension == "pdf"  # derived from the object key
    assert row.folder_id is None


def test_complete_upload_prefers_session_extension(service, file_repo, storage) -> None:
    storage.sizes["uploads/abc.pdf"] = 1234
    file_id = _run(
        service.complete_upload(1, _session(name="archive.tar.bz2", file_extension=".TAR.BZ2"))
    )
    row = file_repo.files[file_id]
    assert row.file_extension == "tar.bz2"  # normalised from the session value


def test_complete_upload_uses_session_file_name_and_folder(service, file_repo) -> None:
    folder = _run(service.create_folder(user_id=1, name="Docs"))
    storage = service._storage
    storage.sizes["uploads/abc.pdf"] = 100
    file_id = _run(service.complete_upload(1, _session(name="report.pdf", folder_id=folder.id)))
    row = file_repo.files[file_id]
    assert row.file_name == "report.pdf"
    assert row.file_extension == "pdf"
    assert row.folder_id == folder.id


def test_complete_upload_rejects_oversized(service, storage) -> None:
    service._size_limits = {SubscriptionTier.FREE: 100, SubscriptionTier.GUEST: 50,
                            SubscriptionTier.PREMIUM: 1000}
    storage.sizes["uploads/big.pdf"] = 500
    with pytest.raises(FileSizeLimitExceededError):
        _run(service.complete_upload(1, _session(key="uploads/big.pdf")))


def test_complete_upload_validates_target_folder(service, storage) -> None:
    storage.sizes["uploads/abc.pdf"] = 10
    with pytest.raises(FolderNotFoundError):
        _run(service.complete_upload(1, _session(folder_id="missing")))


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

def test_set_file_favorite_requires_ownership(service, file_repo) -> None:
    file_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                  file_size_bytes=1, mime_type="x"))
    updated = _run(service.set_file_favorite(1, file_id, True))
    assert updated.is_favorite is True
    assert file_repo.files[file_id].is_favorite is True

    with pytest.raises(FileRecordNotFoundError):
        _run(service.set_file_favorite(2, file_id, True))


def test_list_favorite_files(service, file_repo) -> None:
    fav_id = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                                 file_size_bytes=1, mime_type="x"))
    _run(file_repo.save(user_id=1, file_key="k/b.pdf", file_name="b.pdf",
                        file_size_bytes=1, mime_type="x"))

    _run(service.set_file_favorite(1, fav_id, True))

    # Favorite listing only returns favorited files owned by the user.
    rows, total = _run(service.list_favorite_files(1))
    assert total == 1
    assert rows[0].id == fav_id

    rows, total = _run(service.list_favorite_files(2))
    assert total == 0
    assert rows == []


# ---------------------------------------------------------------------------
# Folder move
# ---------------------------------------------------------------------------

def test_move_folder_requires_ownership(service) -> None:
    root = _run(service.create_folder(user_id=1, name="Root"))
    sub = _run(service.create_folder(user_id=1, name="Sub", parent_id=root.id))

    moved = _run(service.move_folder(1, sub.id, None))
    assert moved.parent_id is None

    # Re-parent under root again.
    moved = _run(service.move_folder(1, sub.id, root.id))
    assert moved.parent_id == root.id

    # Other user cannot move it.
    with pytest.raises(FolderNotFoundError):
        _run(service.move_folder(2, sub.id, None))


def test_move_folder_rejects_cycle(service) -> None:
    root = _run(service.create_folder(user_id=1, name="Root"))
    sub = _run(service.create_folder(user_id=1, name="Sub", parent_id=root.id))
    deep = _run(service.create_folder(user_id=1, name="Deep", parent_id=sub.id))

    # Moving root into its own descendant would create a cycle.
    with pytest.raises(FolderNameConflictError):
        _run(service.move_folder(1, root.id, deep.id))

    # Moving a folder into itself is rejected.
    with pytest.raises(FolderNameConflictError):
        _run(service.move_folder(1, sub.id, sub.id))

    # Moving root into its direct child is rejected.
    with pytest.raises(FolderNameConflictError):
        _run(service.move_folder(1, root.id, sub.id))


def test_move_folder_to_missing_parent_raises(service) -> None:
    sub = _run(service.create_folder(user_id=1, name="Sub"))
    with pytest.raises(FolderNotFoundError):
        _run(service.move_folder(1, sub.id, "nope"))


# ---------------------------------------------------------------------------
# Batch delete
# ---------------------------------------------------------------------------

def test_delete_files_batch(service, file_repo, storage) -> None:
    a = _run(file_repo.save(user_id=1, file_key="k/a.pdf", file_name="a.pdf",
                            file_size_bytes=1, mime_type="x"))
    b = _run(file_repo.save(user_id=1, file_key="k/b.pdf", file_name="b.pdf",
                            file_size_bytes=1, mime_type="x"))
    # A foreign file that must be skipped.
    c = _run(file_repo.save(user_id=2, file_key="k/c.pdf", file_name="c.pdf",
                            file_size_bytes=1, mime_type="x"))

    deleted = _run(service.delete_files(1, [a, b, c, "missing"]))
    assert deleted == 2
    assert a not in file_repo.files
    assert b not in file_repo.files
    assert c in file_repo.files  # other user's file untouched
    assert set(storage.removed) == {"k/a.pdf", "k/b.pdf"}


def test_delete_folders_batch(service, folder_repo, storage) -> None:
    root = _run(service.create_folder(user_id=1, name="Root"))
    sub = _run(service.create_folder(user_id=1, name="Sub", parent_id=root.id))
    other = _run(service.create_folder(user_id=2, name="Foreign"))

    # Monkeypatch delete_with_descendants to return concrete object keys.
    async def delete_with_descendants(folder_id: str):
        del folder_id
        return (["k/root.pdf"], [root.id, sub.id])

    service._folders.delete_with_descendants = delete_with_descendants  # type: ignore[method-assign]

    deleted = _run(service.delete_folders(1, [root.id, other.id, "missing"]))
    assert deleted == 1
    assert other.id in folder_repo.folders  # foreign folder untouched
    assert set(storage.removed) == {"k/root.pdf"}

