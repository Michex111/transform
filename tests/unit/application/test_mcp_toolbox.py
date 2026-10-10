"""The MCP tool layer: scope gating and ownership delegation.

The theme of every test here is that the *tool* must never be the weak link.
The toolbox is thin by design — the rules live in ``FileService`` /
``ConversionService`` — so these tests assert two things: that a missing scope
stops the call before any data is touched, and that the arguments the toolbox
passes to the services are derived from the caller's own rows rather than from
the model's input.
"""

import asyncio
import base64
from typing import Any

from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.services.credit_service import CreditBalance
from src.application.services.mcp_toolbox import MCPToolBox, MCPToolContext
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess, HistoryScope
from src.domain.security.value_object.agent_scope import AgentScope
from src.infrastructure.database.models import UserFileModel

CONVERSION_MAP = {"docx": ["pdf", "txt"], "pdf": ["png"]}

#: A tiny folder forest used by the confinement tests:
#:
#:   bound ── child         other
#:
#: ``bound`` is the folder a restricted grant is confined to; ``child`` is a
#: descendant (reachable); ``other`` is outside the subtree.
FOLDER_PARENTS: dict[str, str | None] = {"bound": None, "child": "bound", "other": None}

#: Files placed in each region of that forest (plus root), so a test can assert
#: exactly which ones a confined agent can and cannot reach.
FOLDER_FILES: dict[str, UserFileModel] = {
    "in-bound": UserFileModel(
        id="in-bound", user_id=7, file_key="upload/in-bound/a.docx", file_name="a.docx",
        file_extension="docx", file_size_bytes=10, mime_type="application/octet-stream",
        folder_id="bound",
    ),
    "in-child": UserFileModel(
        id="in-child", user_id=7, file_key="upload/in-child/b.docx", file_name="b.docx",
        file_extension="docx", file_size_bytes=10, mime_type="application/octet-stream",
        folder_id="child",
    ),
    "elsewhere": UserFileModel(
        id="elsewhere", user_id=7, file_key="upload/elsewhere/c.docx", file_name="c.docx",
        file_extension="docx", file_size_bytes=10, mime_type="application/octet-stream",
        folder_id="other",
    ),
    "at-root": UserFileModel(
        id="at-root", user_id=7, file_key="upload/at-root/d.docx", file_name="d.docx",
        file_extension="docx", file_size_bytes=10, mime_type="application/octet-stream",
        folder_id=None,
    ),
}



def _file(file_id: str = "file-1", owner: int = 7, **overrides: Any) -> UserFileModel:
    values: dict[str, Any] = {
        "id": file_id,
        "user_id": owner,
        "file_key": f"upload/{file_id}/secret-key.docx",
        "file_name": "resume.docx",
        "file_extension": "docx",
        "file_size_bytes": 1234,
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "folder_id": None,
    }
    values.update(overrides)
    return UserFileModel(**values)


def _job(**overrides: Any) -> ConversionJob:
    values: dict[str, Any] = {
        "job_id": "job-1",
        "conversion": ConversionType(source_format="docx", target_format="pdf"),
        "input_file": "resume.docx",
        "output_file": "output/resume.pdf",
        "object_key": "upload/file-1/secret-key.docx",
        "status": JobStatus.COMPLETED,
        "user_id": 7,
        "output_size_bytes": 4321,
    }
    values.update(overrides)
    return ConversionJob(**values)


class FakeFileService:
    def __init__(
        self,
        files: dict[str, UserFileModel] | None = None,
        folders: dict[str, str | None] | None = None,
    ) -> None:
        self.files = files or {}
        #: folder_id -> parent_id (or None for a root folder). Used by
        #: ``resolve_folder_scope`` to mirror the real repository's subtree walk.
        self.folders = folders or {}
        self.deleted: list[str] = []
        self.completed: list[Any] = []

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        row = self.files.get(file_id)
        # Mirrors FileService: a foreign row is indistinguishable from a
        # missing one.
        if row is None or row.user_id != user_id:
            raise FileRecordNotFoundError("File not found")
        return row

    async def list_all_files(self, user_id: int, **kwargs: Any) -> tuple[list[UserFileModel], int]:
        rows = [row for row in self.files.values() if row.user_id == user_id]
        return rows, len(rows)

    async def list_files(self, user_id: int, folder_id: str | None = None, **kwargs: Any):
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id and row.folder_id == folder_id
        ]
        return rows, len(rows)

    async def search_files(self, user_id: int, query: str, **kwargs: Any):
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id and query.lower() in row.file_name.lower()
        ]
        folder_ids = kwargs.get("folder_ids")
        if folder_ids is not None:
            allowed = set(folder_ids)
            rows = [row for row in rows if row.folder_id in allowed]
        return rows, len(rows)

    async def resolve_folder_scope(self, user_id: int, folder_id: str) -> set[str] | None:
        if folder_id not in self.folders:
            return None
        subtree = {folder_id}
        frontier = [folder_id]
        while frontier:
            children = [f for f, parent in self.folders.items() if parent in frontier]
            new = [child for child in children if child not in subtree]
            subtree.update(new)
            frontier = new
        return subtree

    async def authorize_upload_size(self, user_id: int, declared_size: int | None) -> int:
        return 10 * 1024 * 1024

    async def delete_file(self, user_id: int, file_id: str) -> None:
        await self.get_file(user_id, file_id)
        self.deleted.append(file_id)

    async def complete_upload(self, user_id: int, session: Any) -> str:
        self.completed.append(session)
        return "saved-file-1"


class FakeConversionService:
    def __init__(self, jobs: dict[str, ConversionJob] | None = None) -> None:
        self.jobs = jobs or {}
        self.started: list[dict[str, Any]] = []

    async def convert_library_file(self, **kwargs: Any) -> ConversionJob:
        self.started.append(kwargs)
        return _job(status=JobStatus.PENDING, job_id="job-new")

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.jobs.get(job_id)

    async def list_history(
        self, user_id: int, *, offset: int = 0, limit: int = 20,
        origin: JobOrigin | None = None,
    ) -> tuple[list[ConversionJob], int]:
        jobs = [job for job in self.jobs.values() if job.user_id == user_id]
        if origin is not None:
            jobs = [job for job in jobs if job.origin == origin]
        return jobs[offset:offset + limit], len(jobs)


class FakeTransferService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.verified: list[str] = []
        self.deleted_sessions: list[str] = []

    async def create_upload(
        self,
        file_extension: str,
        user_id: str,
        file_name: str | None = None,
        folder_id: str | None = None,
        file_size: int | None = None,
        max_file_size_bytes: int | None = None,
    ) -> Any:
        self.created.append(
            {
                "extension": file_extension,
                "user_id": user_id,
                "file_name": file_name,
                "folder_id": folder_id,
            }
        )

        class _Session:
            upload_id = "upload-1"
            object_key = "upload/upload-1/resume.pdf"

        return _Session()

    async def verify_upload_completion(self, upload_id: str, parts: Any = None):
        self.verified.append(upload_id)
        return "session"

    async def delete_upload_session(self, upload_id: str) -> None:
        self.deleted_sessions.append(upload_id)


class FakeCreditService:
    def __init__(self, balance: int = 42, resets_at: Any = None) -> None:
        self.balance = balance
        self.resets_at = resets_at

    async def get_balance(self, user_id: int) -> CreditBalance:
        return CreditBalance(
            balance=self.balance, total_available=self.balance, resets_at=self.resets_at,
        )


class FakeObjectStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.puts: list[tuple[str, bytes]] = []

    async def put_object(self, object_key: str, data: bytes) -> None:
        self.puts.append((object_key, data))
        self.objects[object_key] = data

    async def read_object(self, object_key: str, max_bytes: int | None = None) -> bytes:
        if object_key not in self.objects:
            raise FileNotFoundError(object_key)
        data = self.objects[object_key]
        return data if max_bytes is None else data[:max_bytes]



class FakeCopier:
    def __init__(self) -> None:
        self.copied: list[tuple[str, str]] = []

    async def copy_object(self, source_key: str, target_key: str) -> int:
        self.copied.append((source_key, target_key))
        return 10


class FakeSubscriptionFree:
    """Placeholder so the tier is explicit in the context."""

    pass


def _build_toolbox(**kwargs: Any) -> tuple[
    MCPToolBox, FakeFileService, FakeConversionService, FakeTransferService,
    FakeCopier, FakeCreditService, FakeObjectStore,
]:
    """Build the toolbox with every collaborator, returning all of them.

    The narrower :func:`_toolbox` returns only the historical five so existing
    tests keep their unpacking; the confinement tests need the credit service
    and the object store too.
    """
    files = kwargs.pop("files", FakeFileService({"file-1": _file()}))
    conversions = kwargs.pop("conversions", FakeConversionService())
    transfers = FakeTransferService()
    copier = FakeCopier()
    credits = kwargs.pop("credits", FakeCreditService())
    store = kwargs.pop("store", FakeObjectStore())
    toolbox = MCPToolBox(
        file_service=files,
        conversion_service=conversions,
        transfer_service=transfers,
        copier=copier,
        credits=credits,
        object_store=store,
        conversion_map=CONVERSION_MAP,
    )
    return toolbox, files, conversions, transfers, copier, credits, store


def _toolbox(**kwargs: Any) -> tuple[
    MCPToolBox, FakeFileService, FakeConversionService, FakeTransferService, FakeCopier,
]:
    toolbox, files, conversions, transfers, copier, _credits, _store = _build_toolbox(**kwargs)
    return toolbox, files, conversions, transfers, copier


def _ctx(
    *scopes: AgentScope,
    user_id: int = 7,
    folder_access: FolderAccess = FolderAccess.ALL,
    folder_id: str | None = None,
    history_scope: HistoryScope = HistoryScope.AGENT,
) -> MCPToolContext:
    return MCPToolContext(
        user_id=user_id,
        scopes=tuple(scopes),
        folder_access=folder_access,
        folder_id=folder_id,
        history_scope=history_scope,
    )


def _folder_ctx(
    *scopes: AgentScope,
    folder_id: str | None = "bound",
    history_scope: HistoryScope = HistoryScope.AGENT,
    user_id: int = 7,
) -> MCPToolContext:
    """A context for a grant confined to the ``bound`` folder (by default)."""
    return _ctx(
        *scopes,
        user_id=user_id,
        folder_access=FolderAccess.FOLDER,
        folder_id=folder_id,
        history_scope=history_scope,
    )


def _folder_toolbox() -> tuple[
    MCPToolBox, FakeFileService, FakeConversionService, FakeTransferService,
    FakeCopier, FakeCreditService, FakeObjectStore,
]:
    return _build_toolbox(
        files=FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS)),
        conversions=FakeConversionService({"job-1": _job()}),
    )



# ---------------------------------------------------------------------------
# Scope gating
# ---------------------------------------------------------------------------


def test_list_files_requires_the_read_scope() -> None:
    async def _run() -> None:
        toolbox, _files, _c, _t, _c2 = _toolbox()
        result = await toolbox.list_files(_ctx(AgentScope.DOCUMENTS_CONVERT))
        assert result["ok"] is False
        assert result["required_scope"] == "documents.read"

    asyncio.run(_run())


def test_convert_without_the_convert_scope_never_reaches_the_service() -> None:
    async def _run() -> None:
        toolbox, _files, conversions, _t, _c = _toolbox()
        result = await toolbox.convert_file(
            _ctx(AgentScope.DOCUMENTS_READ), "file-1", "pdf"
        )
        assert result["ok"] is False
        assert result["required_scope"] == "documents.convert"
        assert conversions.started == []

    asyncio.run(_run())


def test_delete_without_the_delete_scope_deletes_nothing() -> None:
    async def _run() -> None:
        toolbox, files, _c, _t, _c2 = _toolbox()
        result = await toolbox.delete_file(
            _ctx(AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_CONVERT), "file-1"
        )
        assert result["ok"] is False
        assert result["required_scope"] == "documents.delete"
        assert files.deleted == []

    asyncio.run(_run())


def test_save_without_the_write_scope_saves_nothing() -> None:
    async def _run() -> None:
        toolbox, _files, _c, transfers, copier = _toolbox()
        result = await toolbox.save_file(_ctx(AgentScope.DOCUMENTS_READ), "job-1")
        assert result["ok"] is False
        assert result["required_scope"] == "documents.write"
        assert transfers.created == []
        assert copier.copied == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Ownership delegation
# ---------------------------------------------------------------------------


def test_another_users_file_is_reported_as_not_found() -> None:
    async def _run() -> None:
        files = FakeFileService({"file-1": _file(owner=8)})
        toolbox, _files, _c, _t, _c2 = _toolbox(files=files)
        result = await toolbox.get_file(_ctx(AgentScope.DOCUMENTS_READ, user_id=7), "file-1")
        assert result["ok"] is False
        assert result["error"] == "No such file was found in your Drive."

    asyncio.run(_run())


def test_another_users_conversion_is_reported_as_not_found() -> None:
    async def _run() -> None:
        conversions = FakeConversionService({"job-1": _job(user_id=8)})
        toolbox, _files, _c, _t, _c2 = _toolbox(conversions=conversions)
        result = await toolbox.get_conversion_status(_ctx(AgentScope.DOCUMENTS_READ), "job-1")
        assert result["ok"] is False
        assert result["error"] == "No such conversion was found."

    asyncio.run(_run())


def test_no_tool_returns_a_storage_key() -> None:
    async def _run() -> None:
        toolbox, _files, conversions, _t, _c = _toolbox(
            conversions=FakeConversionService({"job-1": _job()})
        )
        listing = await toolbox.list_files(_ctx(AgentScope.DOCUMENTS_READ))
        status = await toolbox.get_conversion_status(_ctx(AgentScope.DOCUMENTS_READ), "job-1")
        blob = repr(listing) + repr(status)
        assert "secret-key" not in blob
        assert "file_key" not in blob
        assert "object_key" not in blob

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Conversion arguments
# ---------------------------------------------------------------------------


def test_convert_resolves_the_object_key_from_the_owned_row() -> None:
    async def _run() -> None:
        toolbox, _files, conversions, _t, _c = _toolbox()
        result = await toolbox.convert_file(
            _ctx(AgentScope.DOCUMENTS_CONVERT), "file-1", "pdf"
        )
        assert result["ok"] is True
        assert conversions.started[0]["object_key"] == "upload/file-1/secret-key.docx"
        assert conversions.started[0]["user_id"] == 7
        assert conversions.started[0]["source_format"] == "docx"

    asyncio.run(_run())


def test_convert_of_an_unsupported_pair_lists_what_is_possible() -> None:
    async def _run() -> None:
        toolbox, _files, conversions, _t, _c = _toolbox()
        result = await toolbox.convert_file(
            _ctx(AgentScope.DOCUMENTS_CONVERT), "file-1", "mp3"
        )
        assert result["ok"] is False
        assert result["supported_target_formats"] == ["pdf", "txt"]
        assert conversions.started == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# save_file
# ---------------------------------------------------------------------------


def test_save_file_refuses_an_unfinished_conversion() -> None:
    async def _run() -> None:
        conversions = FakeConversionService({"job-1": _job(status=JobStatus.PROCESSING)})
        toolbox, _files, _c, transfers, copier = _toolbox(conversions=conversions)
        result = await toolbox.save_file(_ctx(AgentScope.DOCUMENTS_WRITE), "job-1")
        assert result["ok"] is False
        assert transfers.created == []
        assert copier.copied == []

    asyncio.run(_run())


def test_save_file_refuses_a_client_encrypted_output() -> None:
    async def _run() -> None:
        conversions = FakeConversionService({"job-1": _job(client_encrypted=True)})
        toolbox, _files, _c, transfers, copier = _toolbox(conversions=conversions)
        result = await toolbox.save_file(_ctx(AgentScope.DOCUMENTS_WRITE), "job-1")
        assert result["ok"] is False
        assert "encrypted" in result["error"]
        assert transfers.created == []

    asyncio.run(_run())


def test_save_file_copies_the_produced_output_into_the_library() -> None:
    async def _run() -> None:
        conversions = FakeConversionService({"job-1": _job()})
        files = FakeFileService(
            {"saved-file-1": _file("saved-file-1", file_name="resume.pdf", file_extension="pdf")}
        )
        toolbox, _files, _c, transfers, copier = _toolbox(
            files=files, conversions=conversions
        )
        result = await toolbox.save_file(_ctx(AgentScope.DOCUMENTS_WRITE), "job-1")
        assert result["ok"] is True
        # The reservation, the copy and the commit all used the same key.
        assert transfers.created[0]["extension"] == "pdf"
        assert copier.copied == [("output/resume.pdf", "upload/upload-1/resume.pdf")]
        assert transfers.verified == ["upload-1"]
        assert files.completed == ["session"]

    asyncio.run(_run())


def test_a_failed_save_releases_the_upload_session() -> None:
    async def _run() -> None:
        class BrokenCopier(FakeCopier):
            async def copy_object(self, source_key: str, target_key: str) -> int:
                raise OSError("storage down")

        conversions = FakeConversionService({"job-1": _job()})
        toolbox, _files, _c, transfers, _copy = _toolbox(conversions=conversions)
        toolbox._copier = BrokenCopier()  # type: ignore[attr-defined]
        result = await toolbox.save_file(_ctx(AgentScope.DOCUMENTS_WRITE), "job-1")
        assert result["ok"] is False
        assert transfers.deleted_sessions == ["upload-1"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


def test_delete_requires_an_owned_file_and_the_delete_scope() -> None:
    async def _run() -> None:
        toolbox, files, _c, _t, _c2 = _toolbox()
        result = await toolbox.delete_file(
            _ctx(AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_DELETE), "file-1"
        )
        assert result["ok"] is True
        assert files.deleted == ["file-1"]

    asyncio.run(_run())


def test_delete_of_a_foreign_file_is_refused_even_with_the_scope() -> None:
    async def _run() -> None:
        files = FakeFileService({"file-1": _file(owner=8)})
        toolbox, _files, _c, _t, _c2 = _toolbox(files=files)
        result = await toolbox.delete_file(
            _ctx(AgentScope.DOCUMENTS_DELETE, user_id=7), "file-1"
        )
        assert result["ok"] is False
        assert files.deleted == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Supported conversions
# ---------------------------------------------------------------------------


def test_supported_conversions_requires_read_and_filters_by_source() -> None:
    async def _run() -> None:
        toolbox, _files, _c, _t, _c2 = _toolbox()
        full = await toolbox.get_supported_conversions(_ctx(AgentScope.DOCUMENTS_READ))
        assert full["source_format_count"] == 2
        one = await toolbox.get_supported_conversions(
            _ctx(AgentScope.DOCUMENTS_READ), "DOCX"
        )
        assert one["target_formats"] == ["pdf", "txt"]
        unknown = await toolbox.get_supported_conversions(
            _ctx(AgentScope.DOCUMENTS_READ), "mp3"
        )
        assert unknown["ok"] is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Folder confinement
# ---------------------------------------------------------------------------

_FOLDER_DENIAL = {"ok": False, "error": "That folder is not available to this connection."}
_FILE_DENIAL = {"ok": False, "error": "No such file was found in your Drive."}


def test_all_access_still_lists_every_file() -> None:
    """A grant with folder_access ALL behaves exactly as before."""

    async def _run() -> None:
        toolbox, *_ = _build_toolbox(
            files=FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS))
        )
        result = await toolbox.list_files(_ctx(AgentScope.DOCUMENTS_READ))
        assert result["ok"] is True
        assert result["total"] == len(FOLDER_FILES)

    asyncio.run(_run())


def test_folder_access_allows_a_file_in_the_bound_folder() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.get_file(_folder_ctx(AgentScope.DOCUMENTS_READ), "in-bound")
        assert result["ok"] is True
        assert result["file"]["file_id"] == "in-bound"

    asyncio.run(_run())


def test_folder_access_denies_a_file_in_another_folder() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.get_file(_folder_ctx(AgentScope.DOCUMENTS_READ), "elsewhere")
        assert result == _FILE_DENIAL

    asyncio.run(_run())


def test_folder_access_allows_a_descendant_folder_file() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.get_file(_folder_ctx(AgentScope.DOCUMENTS_READ), "in-child")
        assert result["ok"] is True

    asyncio.run(_run())


def test_a_root_file_is_unreachable_for_a_folder_grant() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.get_file(_folder_ctx(AgentScope.DOCUMENTS_READ), "at-root")
        assert result == _FILE_DENIAL

    asyncio.run(_run())


def test_outside_folder_denial_is_identical_to_missing_file() -> None:
    """No existence oracle: an unreachable file looks exactly like a missing one."""

    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        ctx = _folder_ctx(AgentScope.DOCUMENTS_READ)
        outside = await toolbox.get_file(ctx, "elsewhere")
        missing = await toolbox.get_file(ctx, "does-not-exist")
        assert outside == missing == _FILE_DENIAL

    asyncio.run(_run())


def test_folder_access_with_no_bound_folder_denies_every_tool() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        ctx = _folder_ctx(
            AgentScope.DOCUMENTS_READ,
            AgentScope.DOCUMENTS_CONVERT,
            AgentScope.DOCUMENTS_WRITE,
            AgentScope.DOCUMENTS_DELETE,
            folder_id=None,
        )
        payload = base64.b64encode(b"hello").decode()
        results = [
            await toolbox.get_supported_conversions(ctx),
            await toolbox.list_files(ctx),
            await toolbox.get_file(ctx, "in-bound"),
            await toolbox.get_conversion_status(ctx, "job-1"),
            await toolbox.get_conversion_history(ctx),
            await toolbox.get_credits(ctx),
            await toolbox.download_file(ctx, "in-bound"),
            await toolbox.convert_file(ctx, "in-bound", "pdf"),
            await toolbox.save_file(ctx, "job-1"),
            await toolbox.upload_file(ctx, "notes.txt", payload),
            await toolbox.delete_file(ctx, "in-bound"),
        ]
        for result in results:
            assert result == _FOLDER_DENIAL

    asyncio.run(_run())


def test_folder_access_to_a_deleted_folder_denies_every_tool() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        ctx = _folder_ctx(
            AgentScope.DOCUMENTS_READ, AgentScope.DOCUMENTS_WRITE, folder_id="ghost",
        )
        payload = base64.b64encode(b"hello").decode()
        assert await toolbox.list_files(ctx) == _FOLDER_DENIAL
        assert await toolbox.get_credits(ctx) == _FOLDER_DENIAL
        assert await toolbox.list_files(ctx, folder_id="bound") == _FOLDER_DENIAL
        assert await toolbox.upload_file(ctx, "notes.txt", payload) == _FOLDER_DENIAL

    asyncio.run(_run())


def test_list_files_restricted_defaults_to_the_bound_folder() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.list_files(_folder_ctx(AgentScope.DOCUMENTS_READ))
        assert result["ok"] is True
        assert [row["file_id"] for row in result["files"]] == ["in-bound"]

    asyncio.run(_run())


def test_list_files_rejects_a_folder_outside_the_subtree() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.list_files(
            _folder_ctx(AgentScope.DOCUMENTS_READ), folder_id="other"
        )
        assert result == _FOLDER_DENIAL

    asyncio.run(_run())


def test_list_files_allows_a_descendant_folder() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.list_files(
            _folder_ctx(AgentScope.DOCUMENTS_READ), folder_id="child"
        )
        assert result["ok"] is True
        assert [row["file_id"] for row in result["files"]] == ["in-child"]

    asyncio.run(_run())


def test_list_files_search_is_confined_to_the_subtree() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.list_files(_folder_ctx(AgentScope.DOCUMENTS_READ), query="docx")
        assert result["ok"] is True
        ids = {row["file_id"] for row in result["files"]}
        assert ids == {"in-bound", "in-child"}  # never "elsewhere" or "at-root"

    asyncio.run(_run())


def test_save_file_cannot_target_a_folder_outside_the_subtree() -> None:
    async def _run() -> None:
        toolbox, _files, _c, transfers, copier, _cr, _st = _folder_toolbox()
        result = await toolbox.save_file(
            _folder_ctx(AgentScope.DOCUMENTS_WRITE), "job-1", folder_id="other"
        )
        assert result == _FOLDER_DENIAL
        assert transfers.created == []
        assert copier.copied == []

    asyncio.run(_run())


def test_save_file_restricted_defaults_to_the_bound_folder() -> None:
    async def _run() -> None:
        files = FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS))
        files.files["saved-file-1"] = _file(
            "saved-file-1", file_name="resume.pdf", file_extension="pdf", folder_id="bound",
        )
        toolbox, _files, _c, transfers, _copy, _cr, _st = _build_toolbox(
            files=files, conversions=FakeConversionService({"job-1": _job()})
        )
        result = await toolbox.save_file(_folder_ctx(AgentScope.DOCUMENTS_WRITE), "job-1")
        assert result["ok"] is True
        assert transfers.created[0]["folder_id"] == "bound"

    asyncio.run(_run())


def test_upload_file_cannot_target_a_folder_outside_the_subtree() -> None:
    async def _run() -> None:
        toolbox, _files, _c, transfers, _copy, _cr, store = _folder_toolbox()
        payload = base64.b64encode(b"hello").decode()
        result = await toolbox.upload_file(
            _folder_ctx(AgentScope.DOCUMENTS_WRITE), "notes.txt", payload, folder_id="other"
        )
        assert result == _FOLDER_DENIAL
        assert transfers.created == []
        assert store.puts == []

    asyncio.run(_run())


def test_upload_file_restricted_defaults_to_the_bound_folder() -> None:
    async def _run() -> None:
        files = FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS))
        files.files["saved-file-1"] = _file(
            "saved-file-1", file_name="notes.txt", file_extension="txt", folder_id="bound",
        )
        toolbox, _files, _c, transfers, _copy, _cr, store = _build_toolbox(files=files)
        payload = base64.b64encode(b"hello").decode()
        result = await toolbox.upload_file(
            _folder_ctx(AgentScope.DOCUMENTS_WRITE), "notes.txt", payload
        )
        assert result["ok"] is True
        assert transfers.created[0]["folder_id"] == "bound"
        assert store.puts[0][1] == b"hello"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Conversion history scope
# ---------------------------------------------------------------------------


def test_conversion_history_defaults_to_agent_scope() -> None:
    async def _run() -> None:
        conversions = FakeConversionService(
            {
                "j-mcp": _job(job_id="j-mcp", origin=JobOrigin.MCP),
                "j-web": _job(job_id="j-web", origin=JobOrigin.WEB),
            }
        )
        toolbox, *_ = _build_toolbox(conversions=conversions)
        result = await toolbox.get_conversion_history(_ctx(AgentScope.DOCUMENTS_READ))
        assert result["ok"] is True
        assert result["scope"] == "AGENT"
        assert [item["job_id"] for item in result["items"]] == ["j-mcp"]
        assert result["total"] == 1
        assert result["returned"] == 1

    asyncio.run(_run())


def test_conversion_history_all_scope_returns_every_origin() -> None:
    async def _run() -> None:
        conversions = FakeConversionService(
            {
                "j-mcp": _job(job_id="j-mcp", origin=JobOrigin.MCP),
                "j-web": _job(job_id="j-web", origin=JobOrigin.WEB),
            }
        )
        toolbox, *_ = _build_toolbox(conversions=conversions)
        result = await toolbox.get_conversion_history(
            _ctx(AgentScope.DOCUMENTS_READ, history_scope=HistoryScope.ALL)
        )
        assert result["scope"] == "ALL"
        assert {item["job_id"] for item in result["items"]} == {"j-mcp", "j-web"}

    asyncio.run(_run())


def test_conversion_history_empty_is_still_ok() -> None:
    async def _run() -> None:
        toolbox, *_ = _build_toolbox(conversions=FakeConversionService())
        result = await toolbox.get_conversion_history(_ctx(AgentScope.DOCUMENTS_READ))
        assert result["ok"] is True
        assert result["items"] == []
        assert result["total"] == 0
        assert result["scope"] == "AGENT"

    asyncio.run(_run())


def test_conversion_history_item_omits_storage_locations() -> None:
    async def _run() -> None:
        conversions = FakeConversionService({"j-mcp": _job(job_id="j-mcp", origin=JobOrigin.MCP)})
        toolbox, *_ = _build_toolbox(conversions=conversions)
        result = await toolbox.get_conversion_history(_ctx(AgentScope.DOCUMENTS_READ))
        blob = repr(result)
        assert "object_key" not in blob
        assert "secret-key" not in blob
        assert result["items"][0]["job_id"] == "j-mcp"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_credits / download_file
# ---------------------------------------------------------------------------


def test_get_credits_returns_the_balance() -> None:
    async def _run() -> None:
        toolbox, *_ = _build_toolbox(credits=FakeCreditService(balance=17))
        result = await toolbox.get_credits(_ctx(AgentScope.DOCUMENTS_READ))
        assert result == {"ok": True, "balance": 17, "resets_at": None}

    asyncio.run(_run())


def test_download_file_returns_base64_content_for_an_allowed_file() -> None:
    async def _run() -> None:
        store = FakeObjectStore({"upload/in-bound/a.docx": b"content"})
        toolbox, *_ = _build_toolbox(
            files=FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS)), store=store
        )
        result = await toolbox.download_file(_folder_ctx(AgentScope.DOCUMENTS_READ), "in-bound")
        assert result["ok"] is True
        assert base64.b64decode(result["content_base64"]) == b"content"
        assert result["size_bytes"] == len(b"content")

    asyncio.run(_run())


def test_download_file_outside_the_folder_looks_missing() -> None:
    async def _run() -> None:
        store = FakeObjectStore({"upload/elsewhere/c.docx": b"content"})
        toolbox, *_ = _build_toolbox(
            files=FakeFileService(dict(FOLDER_FILES), dict(FOLDER_PARENTS)), store=store
        )
        ctx = _folder_ctx(AgentScope.DOCUMENTS_READ)
        assert await toolbox.download_file(ctx, "elsewhere") == _FILE_DENIAL
        assert await toolbox.download_file(ctx, "elsewhere") == await toolbox.download_file(ctx, "nope")

    asyncio.run(_run())


def test_upload_file_rejects_invalid_base64() -> None:
    async def _run() -> None:
        toolbox, *_ = _folder_toolbox()
        result = await toolbox.upload_file(
            _folder_ctx(AgentScope.DOCUMENTS_WRITE), "notes.txt", "not base64!!!"
        )
        assert result["ok"] is False
        assert "base64" in result["error"]

    asyncio.run(_run())

