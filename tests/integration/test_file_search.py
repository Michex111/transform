"""Endpoint tests for the assistant's ``@`` document-search route.

``GET /api/v1/files/search`` backs the composer's ``@`` picker, so the two
properties that matter most are not about ranking — they are about *scope*:

* a caller only ever sees their own files (the picker must not become a way to
  read someone else's drive by guessing a name); and
* a blank query returns nothing, so the endpoint cannot be used to enumerate a
  user's whole drive in one request.

Both are asserted here against the real router with the real ``FileService`` and
the real SQL repository, over in-memory SQLite. Only auth and the session are
overridden — the authorization rule under test is the production one.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generator

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.presentation.api.main as api_main
from src.application.services.file_service import FileService
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.repository.sql_user_file_repo import SQLUserFileRepository
from src.infrastructure.adapters.repository.sql_user_folder_repo import SQLUserFolderRepository
from src.infrastructure.database.models import UserFileModel, UserFolderModel, UserModel
from src.infrastructure.database.session import Base
from src.presentation.api.dependencies.auth_dependencies import get_current_user
from src.presentation.api.dependencies.service_dependencies import get_file_service

#: The authenticated caller. A second user exists to prove isolation.
CALLER_ID = 1
OTHER_ID = 2


@dataclass
class FakeUser:
    id: int


class SqliteBackend:
    """Lazily creates the SQLite engine inside the app's event loop."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._factory: async_sessionmaker | None = None

    async def ensure(self) -> async_sessionmaker:
        if self._factory is None:
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}",
                poolclass=NullPool,
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(bind=engine, expire_on_commit=False)
            async with factory() as session:
                now = datetime.now(UTC)
                for user_id, username in ((CALLER_ID, "caller"), (OTHER_ID, "other")):
                    session.add(
                        UserModel(
                            id=user_id,
                            username=username,
                            email=f"{username}@example.com",
                            hashed_password="x",
                            is_active=True,
                            created_at=now,
                        )
                    )
                await session.flush()

                # The caller's files: one at the root, one filed away, plus a
                # decoy whose name makes the LIKE-escaping case easy to read.
                session.add_all(
                    [
                        _file("f-root", CALLER_ID, "upei residence.pdf"),
                        _file("f-filed", CALLER_ID, "resume.docx", folder_id="folder-1"),
                        _file("f-percent", CALLER_ID, "50%_report.pdf"),
                    ]
                )
                # Another account's file with the SAME name as the caller's, so a
                # leak would be invisible in a result count alone.
                session.add(_file("f-other", OTHER_ID, "upei residence.pdf"))

                session.add(
                    UserFolderModel(
                        id="folder-1",
                        user_id=CALLER_ID,
                        name="Applications",
                        parent_id=None,
                        created_at=now,
                    )
                )
                await session.commit()
            self._factory = factory
        return self._factory


def _file(
    file_id: str,
    user_id: int,
    file_name: str,
    *,
    folder_id: str | None = None,
) -> UserFileModel:
    return UserFileModel(
        id=file_id,
        user_id=user_id,
        folder_id=folder_id,
        file_key=f"upload/{file_id}/{file_name}",
        file_name=file_name,
        file_extension=file_name.rsplit(".", 1)[-1].lower(),
        file_size_bytes=1024,
        mime_type="application/octet-stream",
        is_favorite=False,
        created_at=datetime.now(UTC),
        expires_at=None,
    )


@contextmanager
def search_client(db_path: str) -> Generator[TestClient, None, None]:
    backend = SqliteBackend(db_path)

    async def no_op_initialize_database() -> None:
        return None

    class FakeStorageOps:
        async def stat_object(self, object_key: str) -> dict | None:
            del object_key
            return None

        async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
            del object_key, max_bytes
            return b""

        async def remove_object(self, object_key: str) -> bool:
            del object_key
            return True

    class FakeSubscriptionRepo:
        async def get_tier_for_user(self, user_id: int) -> SubscriptionTier:
            del user_id
            return SubscriptionTier.FREE

    async def override_file_service():
        factory = await backend.ensure()
        async with factory() as session:
            yield FileService(
                file_repository=SQLUserFileRepository(session=session),
                folder_repository=SQLUserFolderRepository(session=session),
                storage=FakeStorageOps(),
                subscription_repository=FakeSubscriptionRepo(),
            )

    original_init = api_main.initialize_database
    api_main.initialize_database = no_op_initialize_database
    api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=CALLER_ID)
    api_main.app.dependency_overrides[get_file_service] = override_file_service

    client = TestClient(api_main.app)
    try:
        yield client
    finally:
        client.close()
        api_main.app.dependency_overrides.clear()
        api_main.initialize_database = original_init


def test_search_matches_a_name_substring(tmp_path) -> None:
    with search_client(str(tmp_path / "search.db")) as client:
        body = client.get("/api/v1/files/search", params={"q": "upei"}).json()
        assert body["total"] == 1
        assert body["files"][0]["file_name"] == "upei residence.pdf"
        # The picker needs the id it will send back as a reference, plus enough
        # metadata to render a row the user can tell apart from a namesake.
        assert body["files"][0]["id"] == "f-root"
        assert body["files"][0]["file_size_bytes"] == 1024


def test_search_is_case_insensitive(tmp_path) -> None:
    with search_client(str(tmp_path / "search.db")) as client:
        for query in ("UPEI", "uPeI", "upei"):
            body = client.get("/api/v1/files/search", params={"q": query}).json()
            assert body["total"] == 1, query


def test_search_finds_files_inside_folders(tmp_path) -> None:
    # The whole point of the drive-wide search: a document the user filed away
    # is still reachable by name. A root-only listing would miss it, which is
    # the bug the folder-scoped `list_files` cannot fix.
    with search_client(str(tmp_path / "search.db")) as client:
        body = client.get("/api/v1/files/search", params={"q": "resume"}).json()
        assert body["total"] == 1
        assert body["files"][0]["folder_id"] == "folder-1"


def test_search_never_returns_another_users_file(tmp_path) -> None:
    # Both accounts own a file called "upei residence.pdf". Only the caller's may
    # come back — a leak here would be invisible in a count alone, so the id is
    # asserted rather than the total.
    with search_client(str(tmp_path / "search.db")) as client:
        body = client.get("/api/v1/files/search", params={"q": "upei residence"}).json()
        ids = {row["id"] for row in body["files"]}
        assert ids == {"f-root"}
        assert "f-other" not in ids


def test_blank_query_returns_nothing(tmp_path) -> None:
    # A search for nothing is not a listing: without this rule the endpoint
    # would be a one-call way to enumerate a user's entire drive.
    with search_client(str(tmp_path / "search.db")) as client:
        for params in ({"q": ""}, {"q": "   "}):
            body = client.get("/api/v1/files/search", params=params).json()
            assert body["total"] == 0, params
            assert body["files"] == []


def test_missing_query_parameter_is_rejected(tmp_path) -> None:
    # `q` is required, so a caller cannot omit it and fall back to "everything".
    with search_client(str(tmp_path / "search.db")) as client:
        assert client.get("/api/v1/files/search").status_code == 422


def test_no_match_returns_an_empty_list(tmp_path) -> None:
    with search_client(str(tmp_path / "search.db")) as client:
        body = client.get("/api/v1/files/search", params={"q": "nothing-like-this"}).json()
        assert body == {"files": [], "total": 0, "page": 1, "page_size": 20}


def test_wildcards_in_the_query_do_not_widen_the_result(tmp_path) -> None:
    # `%` is a LIKE wildcard. Unescaped, "50%" would match every file the user
    # owns — turning a literal name into a full listing.
    with search_client(str(tmp_path / "search.db")) as client:
        body = client.get("/api/v1/files/search", params={"q": "50%"}).json()
        assert body["total"] == 1
        assert body["files"][0]["file_name"] == "50%_report.pdf"

        # A bare wildcard is escaped to mean "a literal percent sign", so it
        # returns the one file whose name contains one — NOT the caller's whole
        # drive (which would be three files: the root file, the filed resume and
        # this one).
        wildcard = client.get("/api/v1/files/search", params={"q": "%"}).json()
        assert wildcard["total"] == 1
        assert wildcard["files"][0]["file_name"] == "50%_report.pdf"

        # `_` is the other LIKE wildcard (any single character). Escaped, it
        # means a literal underscore — so "50_" matches nothing, because the
        # real name is "50%" followed by "_". Unescaped it WOULD have matched
        # (the `_` consuming the `%`), which is the regression this pins.
        underscore = client.get("/api/v1/files/search", params={"q": "50_"}).json()
        assert underscore["total"] == 0

        # The escaped form of that wildcard does find the literal substring.
        literal = client.get("/api/v1/files/search", params={"q": "%_report"}).json()
        assert literal["total"] == 1
        assert literal["files"][0]["file_name"] == "50%_report.pdf"


def test_page_size_is_bounded(tmp_path) -> None:
    # An unbounded page_size would let one request pull the whole drive.
    with search_client(str(tmp_path / "search.db")) as client:
        assert (
            client.get("/api/v1/files/search", params={"q": "upei", "page_size": 500}).status_code
            == 422
        )


def test_search_requires_authentication(tmp_path) -> None:
    # Exercised against the real dependency rather than the override: the route
    # must not be reachable without an identity, because every query it runs is
    # scoped by one.
    with search_client(str(tmp_path / "search.db")) as client:
        api_main.app.dependency_overrides.pop(get_current_user, None)
        try:
            assert client.get("/api/v1/files/search", params={"q": "upei"}).status_code == 401
        finally:
            api_main.app.dependency_overrides[get_current_user] = lambda: FakeUser(id=CALLER_ID)
