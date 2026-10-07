"""The MCP tool layer: scope gating and ownership delegation.

The theme of every test here is that the *tool* must never be the weak link.
The toolbox is thin by design — the rules live in ``FileService`` /
``ConversionService`` — so these tests assert two things: that a missing scope
stops the call before any data is touched, and that the arguments the toolbox
passes to the services are derived from the caller's own rows rather than from
the model's input.
"""

import asyncio
from typing import Any

from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.services.mcp_toolbox import MCPToolBox, MCPToolContext
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.value_object.agent_scope import AgentScope
from src.infrastructure.database.models import UserFileModel

CONVERSION_MAP = {"docx": ["pdf", "txt"], "pdf": ["png"]}


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
    def __init__(self, files: dict[str, UserFileModel] | None = None) -> None:
        self.files = files or {}
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
        return await self.list_all_files(user_id)

    async def search_files(self, user_id: int, query: str, **kwargs: Any):
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id and query.lower() in row.file_name.lower()
        ]
        return rows, len(rows)

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
        self.created.append({"extension": file_extension, "user_id": user_id, "file_name": file_name})

        class _Session:
            upload_id = "upload-1"
            object_key = "upload/upload-1/resume.pdf"

        return _Session()

    async def verify_upload_completion(self, upload_id: str, parts: Any = None):
        self.verified.append(upload_id)
        return "session"

    async def delete_upload_session(self, upload_id: str) -> None:
        self.deleted_sessions.append(upload_id)


class FakeCopier:
    def __init__(self) -> None:
        self.copied: list[tuple[str, str]] = []

    async def copy_object(self, source_key: str, target_key: str) -> int:
        self.copied.append((source_key, target_key))
        return 10


class FakeSubscriptionFree:
    """Placeholder so the tier is explicit in the context."""

    pass


def _toolbox(**kwargs: Any) -> tuple[MCPToolBox, FakeFileService, FakeConversionService, FakeTransferService, FakeCopier]:
    files = kwargs.pop("files", FakeFileService({"file-1": _file()}))
    conversions = kwargs.pop("conversions", FakeConversionService())
    transfers = FakeTransferService()
    copier = FakeCopier()
    toolbox = MCPToolBox(
        file_service=files,
        conversion_service=conversions,
        transfer_service=transfers,
        copier=copier,
        conversion_map=CONVERSION_MAP,
    )
    return toolbox, files, conversions, transfers, copier


def _ctx(*scopes: AgentScope, user_id: int = 7) -> MCPToolContext:
    return MCPToolContext(user_id=user_id, scopes=tuple(scopes))


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
