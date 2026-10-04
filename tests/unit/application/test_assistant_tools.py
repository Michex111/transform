"""Unit tests for the assistant's tools.

The tools are the only way the model can see or change user data, so these tests
pin the two things that matter: ownership is enforced by delegation (a foreign
file is an error, never a result) and every failure comes back as a value the
model can explain rather than an exception that aborts the turn.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.application.dtos.assistant_dto import Artifact
from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.ports.assistant_account_port import AccountOverview
from src.application.ports.document_text_port import ExtractedDocument
from src.application.services.assistant_tools import (
    _MAX_EXTENSION_FILTERS,
    _MAX_LIST_LIMIT,
    AssistantToolBox,
    format_category,
    format_label,
)
from src.domain.assistant.policies.assistant_policy import max_actions_per_turn
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.value_object.conversion_type import ConversionType
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.database.models import UserFileModel, UserFolderModel
from tests.fakes.fake_assistant_model_resolver import FakeAssistantModelResolver
from tests.fakes.fake_llm_port import FakeLlmPort, text_response

NOW = datetime(2026, 9, 28, tzinfo=UTC)
OWNER = 7
OTHER = 99


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeFolderRepository:
    """In-memory folder table, scoped by user like the real repository."""

    def __init__(self, folders: list[UserFolderModel] | None = None) -> None:
        self.folders = {folder.id: folder for folder in folders or []}

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None:
        return self.folders.get(folder_id)

    async def list_by_parent(
        self, user_id: int, parent_id: str | None, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[UserFolderModel], int]:
        rows = [
            folder
            for folder in self.folders.values()
            if folder.user_id == user_id and folder.parent_id == parent_id
        ]
        return rows[offset : offset + limit], len(rows)


class FakeFileService:
    """The slice of ``FileService`` the toolbox uses, with real ownership rules."""

    def __init__(self, files: list[UserFileModel]) -> None:
        self.files = {row.id: row for row in files}
        self.list_calls: list[int] = []
        self.search_calls: list[str] = []
        #: Recorded so a test can prove a whole-drive listing was asked for,
        #: rather than inferred from the rows it happened to return.
        self.list_all_calls: int = 0
        #: Recorded so a test can prove the format filter reached the service
        #: (the repository is what actually applies it) instead of being applied
        #: to an already-fetched page in the toolbox.
        self.extension_filters: list[Sequence[str] | None] = []
        #: Recorded so a test can prove the delete tool never reaches the real
        #: deletion path (it must only ever propose).
        self.deleted: list[str] = []

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        row = self.files.get(file_id)
        if row is None or row.user_id != user_id:
            # Same behaviour (and exception) as the real service: the toolbox
            # must not have a weaker ownership check than the REST endpoints.
            raise FileRecordNotFoundError()
        return row

    @staticmethod
    def _match_extension(row: UserFileModel, extensions: Sequence[str] | None) -> bool:
        """Mirror the repository's SQL ``IN`` over the normalised column."""
        if extensions is None:
            return True
        return (row.file_extension or "") in extensions

    async def list_files(
        self, user_id: int, folder_id: str | None = None, *, offset: int = 0, limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        self.list_calls.append(limit)
        self.extension_filters.append(extensions)
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id
            and row.folder_id == folder_id
            and self._match_extension(row, extensions)
        ]
        return rows[offset : offset + limit], len(rows)

    async def list_all_files(
        self, user_id: int, *, offset: int = 0, limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        self.list_all_calls += 1
        self.extension_filters.append(extensions)
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id and self._match_extension(row, extensions)
        ]
        return rows[offset : offset + limit], len(rows)

    async def search_files(
        self, user_id: int, query: str, *, offset: int = 0, limit: int = 50,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]:
        """Literal, case-insensitive substring search across every folder.

        Deliberately literal (a plain ``in``, not a LIKE), so a test can prove
        the toolbox does not treat ``%``/``_`` as wildcards; the real escaping
        is pinned against the SQL repository.
        """
        self.search_calls.append(query)
        self.extension_filters.append(extensions)
        needle = query.casefold()
        rows = [
            row
            for row in self.files.values()
            if row.user_id == user_id
            and needle in row.file_name.casefold()
            and self._match_extension(row, extensions)
        ]
        return rows[offset : offset + limit], len(rows)

    async def create_folder(
        self, user_id: int, name: str, parent_id: str | None = None
    ) -> UserFolderModel:
        return UserFolderModel(
            id="folder-new",
            user_id=user_id,
            name=name,
            parent_id=parent_id,
            created_at=NOW,
            updated_at=NOW,
        )

    async def move_file(
        self, user_id: int, file_id: str, folder_id: str | None
    ) -> UserFileModel:
        row = await self.get_file(user_id, file_id)
        row.folder_id = folder_id
        return row

    async def delete_file(self, user_id: int, file_id: str) -> None:
        """Remove the row like the real service, so "still present" means something.

        The tool must never call this; if a regression ever made it call, the
        object would disappear from ``files`` and the "not called" assertion
        would be the first thing to fail.
        """
        row = await self.get_file(user_id, file_id)
        self.deleted.append(row.id)
        del self.files[row.id]


class FakeConversionService:
    def __init__(self) -> None:
        self.converted: list[dict] = []
        self.jobs: dict[str, ConversionJob] = {}
        self.search_calls: list[dict] = []

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
        origin: JobOrigin = JobOrigin.WEB,
    ) -> ConversionJob:
        self.converted.append(
            {
                "file_name": file_name,
                "source_format": source_format,
                "target_format": target_format,
                "object_key": object_key,
                "user_id": user_id,
                "tier": tier,
                "origin": origin,
            }
        )
        job = ConversionJob(
            job_id=f"job-{len(self.converted)}",
            conversion=ConversionType(source_format=source_format, target_format=target_format),
            input_file=file_name,
            object_key=object_key,
            user_id=user_id,
            origin=origin,
            status=JobStatus.PENDING,
        )
        self.jobs[job.job_id] = job
        return job

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        return self.jobs.get(job_id)

    async def list_history(
        self, user_id: int, *, offset: int = 0, limit: int = 20, since=None
    ) -> tuple[list[ConversionJob], int]:
        rows = [job for job in self.jobs.values() if job.user_id == user_id]
        return rows[offset : offset + limit], len(rows)

    async def search_jobs(
        self, user_id: int, *, query: str | None = None, fmt: str | None = None, limit: int = 10
    ) -> list[ConversionJob]:
        """Records the filters so a test can prove they were passed through."""
        self.search_calls.append(
            {"user_id": user_id, "query": query, "fmt": fmt, "limit": limit}
        )
        rows = [job for job in self.jobs.values() if job.user_id == user_id]
        if query is not None:
            needle = query.casefold()
            rows = [
                job
                for job in rows
                if needle in (job.input_file or "").casefold()
                or needle in (job.output_file or "").casefold()
            ]
        if fmt is not None:
            wanted = fmt.lstrip(".").lower()
            rows = [
                job
                for job in rows
                if job.conversion.source_format.lower() == wanted
                or job.conversion.target_format.lower() == wanted
            ]
        return rows[:limit]


class FakeStorage:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    async def stat_object(self, object_key: str) -> dict | None:
        if object_key not in self.payloads:
            return None
        return {"size": len(self.payloads[object_key])}

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        return self.payloads.get(object_key, b"")[:max_bytes]


class FakeExtractor:
    def __init__(self, result: ExtractedDocument) -> None:
        self.result = result
        self.calls: list[tuple[str, bytes]] = []

    def extract(self, *, file_name: str, data: bytes) -> ExtractedDocument:
        self.calls.append((file_name, data))
        return self.result


class FakeAccountPort:
    """Records the filters the account tool passes and returns a fixed snapshot."""

    def __init__(self, overview: AccountOverview | None = None) -> None:
        self.overview_result = overview or AccountOverview(
            tier="FREE",
            credits_remaining=42,
            credits_reset_at=datetime(2026, 10, 1, tzinfo=UTC),
            storage_used_bytes=1024,
            storage_limit_bytes=5 * 1024**3,
            jobs_total=4,
            jobs_completed=2,
            jobs_failed=1,
            jobs_active=1,
            by_target_format=(("pdf", 3), ("txt", 1)),
        )
        self.calls: list[dict] = []

    async def overview(
        self,
        user_id: int,
        *,
        since: datetime | None,
        fmt: str | None,
        status: str | None,
    ) -> AccountOverview:
        self.calls.append(
            {"user_id": user_id, "since": since, "fmt": fmt, "status": status}
        )
        return self.overview_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _file(
    file_id: str = "file-1",
    *,
    user_id: int = OWNER,
    name: str = "report.pdf",
    extension: str = "pdf",
    folder_id: str | None = None,
    key: str = "objects/report.pdf",
) -> UserFileModel:
    return UserFileModel(
        id=file_id,
        user_id=user_id,
        folder_id=folder_id,
        file_key=key,
        file_name=name,
        file_extension=extension,
        file_size_bytes=1024,
        mime_type="application/pdf",
        is_favorite=False,
        created_at=NOW,
    )


def _folder(
    folder_id: str, name: str, *, parent_id: str | None = None, user_id: int = OWNER
) -> UserFolderModel:
    return UserFolderModel(
        id=folder_id,
        user_id=user_id,
        name=name,
        parent_id=parent_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _toolbox(
    *,
    files: list[UserFileModel] | None = None,
    folders: list[UserFolderModel] | None = None,
    extraction: ExtractedDocument | None = None,
    payload: bytes = b"document text",
    llm: FakeLlmPort | None = None,
    models: FakeAssistantModelResolver | None = None,
    conversion_service: FakeConversionService | None = None,
    account_port: FakeAccountPort | None = None,
    max_document_bytes: int = 10 * 1024 * 1024,
) -> AssistantToolBox:
    rows = files if files is not None else [_file()]
    return AssistantToolBox(
        file_service=FakeFileService(rows),
        conversion_service=conversion_service or FakeConversionService(),
        folder_repository=FakeFolderRepository(folders),
        storage=FakeStorage({row.file_key: payload for row in rows}),
        extractor=FakeExtractor(
            extraction or ExtractedDocument(text="hello world", truncated=False, supported=True)
        ),
        models=models
        or FakeAssistantModelResolver(
            default=llm
            or FakeLlmPort([text_response('{"summary": "s", "key_points": ["p"]}')])
        ),
        account_port=account_port or FakeAccountPort(),
        max_document_bytes=max_document_bytes,
        summary_max_input_chars=1000,
    )


def _run(
    box: AssistantToolBox,
    name: str,
    arguments: dict | None = None,
    *,
    tier: SubscriptionTier = SubscriptionTier.FREE,
    conversation_id: str | None = None,
) -> tuple[dict, list[Artifact]]:
    artifacts: list[Artifact] = []
    result = asyncio.run(
        box.execute(
            name,
            arguments or {},
            user_id=OWNER,
            tier=tier,
            artifacts=artifacts,
            conversation_id=conversation_id,
        )
    )
    return result, artifacts


# ---------------------------------------------------------------------------
# Specs and metadata
# ---------------------------------------------------------------------------


def test_every_tool_has_a_spec_a_label_and_a_schema() -> None:
    box = _toolbox()
    specs = {spec.name: spec for spec in box.specs()}
    assert set(specs) == {
        "list_files",
        "list_folders",
        "get_file_info",
        "list_supported_targets",
        "read_file_text",
        "summarize_file",
        "start_conversion",
        "get_conversion_status",
        "list_recent_conversions",
        "create_folder",
        "move_file",
        "delete_file",
        "get_account_overview",
    }
    for spec in specs.values():
        assert spec.description
        assert spec.parameters["type"] == "object"
        # Rejecting unknown arguments is what stops the model from inventing one
        # that the tool would silently ignore.
        assert spec.parameters["additionalProperties"] is False
        assert box.label_for(spec.name) != "Working on it"


def test_unknown_tool_is_an_error_value() -> None:
    result, _ = _run(_toolbox(), "nonexistent")
    assert "error" in result


def test_start_conversion_does_not_advertise_a_destination_folder() -> None:
    """The tool must not offer a folder it cannot honour.

    A library conversion writes its output to an object key the worker owns;
    nothing in the API files a finished output into a drive folder (the SPA's
    "Save to Drive" does that in the browser). If the schema advertised a
    ``folder_id``, the model would accept it and tell the user the result was
    filed there, which would be false. The argument is therefore absent, and
    the description says so.
    """
    spec = next(spec for spec in _toolbox().specs() if spec.name == "start_conversion")
    assert "folder_id" not in spec.parameters["properties"]
    assert "not" in spec.description.lower()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_files_filters_by_query_and_appends_artifacts() -> None:
    box = _toolbox(
        files=[
            _file("file-1", name="quarterly-report.pdf"),
            _file("file-2", name="notes.txt", extension="txt", key="objects/notes.txt"),
        ]
    )
    result, artifacts = _run(box, "list_files", {"query": "REPORT"})
    assert "error" not in result
    assert result["count"] == 1
    assert result["files"][0]["file_name"] == "quarterly-report.pdf"
    # A bare query searches every folder, and says so, so the model can tell
    # the user where it looked.
    assert result["scope"] == "all_folders"
    assert [artifact.id for artifact in artifacts] == ["file-1"]
    assert artifacts[0].type == "file"


def test_list_files_query_finds_a_file_inside_a_folder() -> None:
    """The regression this change exists for: a filed document is findable.

    ``list_files`` with an omitted folder used to resolve to the root listing,
    so "find my invoice" could never see ``Invoices/invoice.pdf``.
    """
    box = _toolbox(files=[_file("file-1", name="invoice-2026.pdf", folder_id="folder-1")])
    result, artifacts = _run(box, "list_files", {"query": "invoice"})
    assert "error" not in result
    assert result["scope"] == "all_folders"
    assert result["count"] == 1
    assert result["files"][0]["folder_id"] == "folder-1"
    assert [artifact.id for artifact in artifacts] == ["file-1"]


def test_list_files_query_wildcards_stay_literal() -> None:
    """``%`` and ``_`` in a query must not act as SQL wildcards.

    ``axb-report.pdf`` matches ``a_b`` only if ``_`` is treated as a wildcard,
    and ``50X.pdf`` matches ``50%`` only if ``%`` is; both must be excluded.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="axb-report.pdf"),
            _file("file-2", name="a_b-report.pdf", key="objects/a_b-report.pdf"),
            _file("file-3", name="50X.pdf", key="objects/50X.pdf"),
            _file("file-4", name="50%.pdf", key="objects/50%.pdf"),
        ]
    )
    underscore, _ = _run(box, "list_files", {"query": "a_b"})
    assert [row["file_name"] for row in underscore["files"]] == ["a_b-report.pdf"]
    percent, _ = _run(box, "list_files", {"query": "50%"})
    assert [row["file_name"] for row in percent["files"]] == ["50%.pdf"]


def test_list_files_clamps_the_model_supplied_limit() -> None:
    """A model-authored ``limit`` is a hint; an unbounded query is not allowed."""
    box = _toolbox()
    result, _ = _run(box, "list_files", {"limit": 10_000})
    assert "error" not in result
    service = box._files
    assert isinstance(service, FakeFileService)
    assert service.list_calls == [_MAX_LIST_LIMIT]


def test_list_files_resolves_a_folder_by_name_case_insensitively() -> None:
    box = _toolbox(
        files=[_file("file-1", folder_id="folder-1")],
        folders=[_folder("folder-1", "Invoices")],
    )
    result, _ = _run(box, "list_files", {"folder": "invoices"})
    assert "error" not in result
    assert result["count"] == 1
    # A folder-scoped listing stays scoped — the whole-drive search is opt-in
    # via an omitted folder plus a query.
    assert result["scope"] == "folder"


def test_list_files_reports_ambiguous_folder_names() -> None:
    box = _toolbox(folders=[_folder("folder-1", "Reports"), _folder("folder-2", "Reports")])
    result, _ = _run(box, "list_files", {"folder": "reports"})
    assert "error" in result
    assert "reports" in result["error"]


def test_list_files_reports_a_missing_folder_with_the_known_names() -> None:
    box = _toolbox(folders=[_folder("folder-1", "Invoices")])
    result, _ = _run(box, "list_files", {"folder": "Taxes"})
    assert "error" in result
    assert "Invoices" in result["error"]


# ---------------------------------------------------------------------------
# Format filters ("List my PDFs")
# ---------------------------------------------------------------------------


def test_list_files_by_extension_returns_only_that_format() -> None:
    """The reported bug: "List my PDFs" attached Word documents to the answer.

    A bare listing returns every file, so the model could only narrow the answer
    in prose — while the artifacts chipped onto that answer came from the
    UNFILTERED rows. An `extension` filter makes the rows themselves the answer,
    so the two can no longer disagree.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="resume.pdf", extension="pdf"),
            _file("file-2", name="resume.docx", extension="docx", key="objects/resume.docx"),
            _file("file-3", name="notes.txt", extension="txt", key="objects/notes.txt"),
        ]
    )
    result, artifacts = _run(box, "list_files", {"extension": "pdf"})
    assert "error" not in result
    assert [row["file_name"] for row in result["files"]] == ["resume.pdf"]
    assert result["extension"] == "pdf"
    # The chips are the same rows as the answer, so no unrelated file is attached.
    assert [artifact.name for artifact in artifacts] == ["resume.pdf"]


def test_list_files_by_extension_searches_every_folder() -> None:
    """A filed-away PDF is still one of the user's PDFs.

    Without the whole-drive scope the filter would answer from the root and
    silently omit exactly the files the user filed away — the same defect that
    ``query`` had before it was fixed.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="root.pdf", extension="pdf"),
            _file(
                "file-2",
                name="filed.pdf",
                extension="pdf",
                folder_id="folder-1",
                key="objects/filed.pdf",
            ),
        ]
    )
    result, _ = _run(box, "list_files", {"extension": "pdf"})
    assert result["scope"] == "all_folders"
    assert sorted(row["file_name"] for row in result["files"]) == ["filed.pdf", "root.pdf"]
    service = box._files
    assert isinstance(service, FakeFileService)
    assert service.list_all_calls == 1
    assert service.extension_filters == [["pdf"]]


def test_list_files_normalises_the_model_supplied_extension() -> None:
    """``".PDF"`` / ``"*.pdf"`` / ``"pdf"`` all mean the same format.

    The filter is compared against the stored column with SQL equality, so a
    value the model spelled with a dot or a wildcard must be normalised rather
    than matching nothing — which would read to the user as "you have no PDFs".
    """
    box = _toolbox(files=[_file("file-1", name="resume.pdf", extension="pdf")])
    for spelling in (".PDF", "PDF", "*.pdf", " pdf ", "application/pdf"):
        result, _ = _run(box, "list_files", {"extension": spelling})
        assert result["extension"] == "pdf", spelling
        assert result["count"] == 1, spelling


def test_list_files_treats_a_filter_that_normalises_to_nothing_as_absent() -> None:
    """``"*"``/``""`` must widen the listing, not silently empty it.

    Emptying the result would be the worst reading: the model would report that
    the user has no files of a format it never actually filtered by.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="resume.pdf", extension="pdf"),
            _file("file-2", name="resume.docx", extension="docx", key="objects/resume.docx"),
        ]
    )
    for spelling in ("*", "", "   "):
        result, _ = _run(box, "list_files", {"extension": spelling})
        assert "extension" not in result, spelling
        assert result["scope"] == "root", spelling
        assert result["count"] == 2, spelling


def test_list_files_combines_extension_with_query_and_folder() -> None:
    """The filters compose, and a folder still narrows them."""
    box = _toolbox(
        files=[
            _file("file-1", name="invoice.pdf", extension="pdf", folder_id="folder-1"),
            _file(
                "file-2",
                name="invoice.docx",
                extension="docx",
                folder_id="folder-1",
                key="objects/invoice.docx",
            ),
            _file("file-3", name="invoice.pdf", extension="pdf", key="objects/invoice-root.pdf"),
        ],
        folders=[_folder("folder-1", "Invoices")],
    )
    scoped, _ = _run(box, "list_files", {"folder": "invoices", "extension": "pdf"})
    assert scoped["scope"] == "folder"
    assert [row["file_id"] for row in scoped["files"]] == ["file-1"]

    searched, _ = _run(box, "list_files", {"query": "invoice", "extension": "docx"})
    assert searched["scope"] == "all_folders"
    assert [row["file_id"] for row in searched["files"]] == ["file-2"]


def test_list_files_says_so_when_a_format_filter_matches_nothing() -> None:
    """An empty format filter must not be read as "you have none of those".

    The note is what stops the model from turning "no rows" into a confident
    claim about the user's drive.
    """
    box = _toolbox(files=[_file("file-1", name="notes.txt", extension="txt")])
    result, artifacts = _run(box, "list_files", {"extension": "pdf"})
    assert result["count"] == 0
    assert artifacts == []
    assert "note" in result
    # The note must name the filter that produced no rows, and must tell the
    # model how to tell "no such files" apart from "that was a category word".
    assert "pdf" in result["note"]
    assert "spreadsheets" in result["note"]


def test_list_files_answers_a_category_with_one_query() -> None:
    """"Do I have any spreadsheets?" is ONE question about several extensions.

    A single-format filter would need a call per format — burning a plan's tool
    budget, and on a small budget coming back half-answered. This is the shape
    the app's own suggested prompt needs.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="budget.xlsx", extension="xlsx"),
            _file("file-2", name="data.csv", extension="csv", key="objects/data.csv"),
            _file("file-3", name="notes.pdf", extension="pdf", key="objects/notes.pdf"),
        ]
    )
    result, artifacts = _run(box, "list_files", {"extension": ["xlsx", "csv", "ods"]})
    assert result["count"] == 2
    assert sorted(row["file_name"] for row in result["files"]) == ["budget.xlsx", "data.csv"]
    assert result["extension"] == ["xlsx", "csv", "ods"]
    service = box._files
    assert isinstance(service, FakeFileService)
    assert service.list_all_calls == 1
    assert service.extension_filters == [["xlsx", "csv", "ods"]]
    # Only the spreadsheets are chipped; the PDF is not part of this answer.
    assert sorted(artifact.name for artifact in artifacts) == ["budget.xlsx", "data.csv"]


def test_list_files_dedupes_and_bounds_the_extension_list() -> None:
    """The list is model-authored, so it is normalised, deduped and capped."""
    box = _toolbox(files=[_file("file-1", name="a.pdf", extension="pdf")])
    result, _ = _run(box, "list_files", {"extension": [".PDF", "pdf", "*.pdf"]})
    assert result["extension"] == "pdf"  # collapsed to one, reported as a scalar
    service = box._files
    assert isinstance(service, FakeFileService)
    assert service.extension_filters == [["pdf"]]

    many = [f"x{i}" for i in range(_MAX_EXTENSION_FILTERS + 5)]
    bounded, _ = _run(box, "list_files", {"extension": many})
    assert len(bounded["extension"]) == _MAX_EXTENSION_FILTERS


def test_list_files_finds_a_file_by_extension_on_this_project() -> None:
    """A concrete case: a PDF the user would call "my PDFs" must be found.

    Reproduces the shape of the reported conversation — a drive holding a PDF
    and a same-named DOCX — and asserts the PDF-only answer carries only the PDF.
    """
    box = _toolbox(
        files=[
            _file("file-1", name="Sales Associate resume(General).pdf", extension="pdf"),
            _file(
                "file-2",
                name="Sales Associate resume(General).docx",
                extension="docx",
                key="objects/resume.docx",
            ),
            _file(
                "file-3",
                name="CS2520 course outline 2026 (2).docx",
                extension="docx",
                key="objects/outline.docx",
            ),
        ]
    )
    result, artifacts = _run(box, "list_files", {"extension": "pdf", "limit": 50})
    assert [row["file_name"] for row in result["files"]] == [
        "Sales Associate resume(General).pdf"
    ]
    # The exact bug: neither .docx may be chipped onto this answer.
    assert [artifact.name for artifact in artifacts] == [
        "Sales Associate resume(General).pdf"
    ]


def test_list_folders_lists_the_root_by_default() -> None:
    result, _ = _run(_toolbox(folders=[_folder("folder-1", "Invoices")]), "list_folders")
    assert result["count"] == 1
    assert result["folders"][0]["name"] == "Invoices"


def test_list_folders_does_not_leak_another_users_folders() -> None:
    box = _toolbox(folders=[_folder("folder-1", "Theirs", user_id=OTHER)])
    result, _ = _run(box, "list_folders")
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_get_file_info_rejects_another_users_file() -> None:
    box = _toolbox(files=[_file("file-1", user_id=OTHER)])
    result, artifacts = _run(box, "get_file_info", {"file_id": "file-1"})
    assert "error" in result
    assert artifacts == []


def test_read_file_text_rejects_another_users_file() -> None:
    box = _toolbox(files=[_file("file-1", user_id=OTHER)])
    result, _ = _run(box, "read_file_text", {"file_id": "file-1"})
    assert "error" in result


def test_start_conversion_rejects_another_users_file() -> None:
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1", user_id=OTHER)], conversion_service=conversions)
    result, _ = _run(box, "start_conversion", {"file_id": "file-1", "target_format": "docx"})
    assert "error" in result
    assert conversions.converted == []


def test_get_conversion_status_hides_another_users_job() -> None:
    conversions = FakeConversionService()
    conversions.jobs["job-x"] = ConversionJob(
        job_id="job-x",
        conversion=ConversionType(source_format="pdf", target_format="docx"),
        input_file="secret.pdf",
        user_id=OTHER,
    )
    result, _ = _run(
        _toolbox(conversion_service=conversions), "get_conversion_status", {"job_id": "job-x"}
    )
    assert "error" in result
    assert "secret.pdf" not in str(result)


# ---------------------------------------------------------------------------
# Formats and conversions
# ---------------------------------------------------------------------------


def test_list_supported_targets_returns_registry_edges() -> None:
    result, _ = _run(_toolbox(), "list_supported_targets", {"source_format": ".PDF"})
    assert result["source_format"] == "pdf"
    assert "docx" in result["targets"]


def test_list_supported_targets_rejects_an_unknown_format() -> None:
    result, _ = _run(_toolbox(), "list_supported_targets", {"source_format": "xyz"})
    assert "error" in result
    assert "pdf" in result["error"]


def test_start_conversion_rejects_an_unsupported_edge() -> None:
    conversions = FakeConversionService()
    box = _toolbox(
        files=[_file("file-1", name="bundle.zip", extension="zip", key="objects/bundle.zip")],
        conversion_service=conversions,
    )
    result, artifacts = _run(box, "start_conversion", {"file_id": "file-1", "target_format": "jpg"})
    assert "error" in result
    assert conversions.converted == []
    assert artifacts == []


def test_start_conversion_enqueues_the_job_and_returns_an_artifact() -> None:
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    result, artifacts = _run(box, "start_conversion", {"file_id": "file-1", "target_format": "DOCX"})
    assert result["target_format"] == "docx"
    assert conversions.converted[0]["source_format"] == "pdf"
    assert conversions.converted[0]["user_id"] == OWNER
    assert conversions.converted[0]["object_key"] == "objects/report.pdf"
    assert conversions.converted[0]["tier"] == SubscriptionTier.FREE
    assert [artifact.type for artifact in artifacts] == ["job"]


def test_start_conversion_threads_the_request_origin_to_the_job() -> None:
    """An API-key assistant turn must be recorded as API, not the WEB default."""
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    artifacts: list[Artifact] = []
    result = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=artifacts,
            origin=JobOrigin.API,
        )
    )
    assert "error" not in result
    assert conversions.converted[0]["origin"] is JobOrigin.API


def test_start_conversion_respects_the_per_turn_action_limit() -> None:
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    spent = [
        Artifact(type="job", id=f"job-{index}", name="x.pdf", meta={"started": True})
        for index in range(max_actions_per_turn(SubscriptionTier.FREE))
    ]
    result = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=spent,
        )
    )
    assert "error" in result
    assert conversions.converted == []


def test_reported_jobs_do_not_consume_the_action_budget() -> None:
    """Reading the job list is not an action, so it must not block a conversion."""
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    reported = [
        Artifact(type="job", id=f"job-{index}", name="x.pdf", meta={"status": "COMPLETED"})
        for index in range(max_actions_per_turn(SubscriptionTier.FREE) * 2)
    ]
    result = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=reported,
        )
    )
    assert "error" not in result
    assert len(conversions.converted) == 1


def test_the_started_conversion_artifact_is_marked_as_an_action() -> None:
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    _, artifacts = _run(box, "start_conversion", {"file_id": "file-1", "target_format": "docx"})
    assert artifacts[0].meta["started"] is True


def test_the_per_turn_action_budget_is_the_callers_tier() -> None:
    """FREE stops at 1 conversion per turn; PRO is allowed 3."""
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    one_spent = [Artifact(type="job", id="job-1", name="x.pdf", meta={"started": True})]

    free = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=list(one_spent),
        )
    )
    assert "error" in free
    assert "at most 1" in free["error"]

    pro = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.PRO,
            artifacts=list(one_spent),
        )
    )
    assert "error" not in pro

    three_spent = [
        Artifact(type="job", id=f"job-{index}", name="x.pdf", meta={"started": True})
        for index in range(3)
    ]
    pro_full = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.PRO,
            artifacts=list(three_spent),
        )
    )
    assert "error" in pro_full
    assert "at most 3" in pro_full["error"]


def test_the_tool_budget_does_not_leak_between_calls() -> None:
    """The toolbox is a shared singleton: the second call uses its own tier's budget."""
    conversions = FakeConversionService()
    box = _toolbox(files=[_file("file-1")], conversion_service=conversions)
    spent = [Artifact(type="job", id="job-1", name="x.pdf", meta={"started": True})]

    refused = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=list(spent),
        )
    )
    assert "error" in refused

    allowed = asyncio.run(
        box.execute(
            "start_conversion",
            {"file_id": "file-1", "target_format": "docx"},
            user_id=OWNER,
            tier=SubscriptionTier.PRO,
            artifacts=list(spent),
        )
    )
    assert "error" not in allowed


def test_the_document_read_budget_is_the_callers_tier() -> None:
    """A FREE caller cannot read a 3 MiB document; a PRO caller can."""
    payload = b"x" * (3 * 1024 * 1024)
    box = _toolbox(payload=payload, max_document_bytes=10 * 1024 * 1024)

    free, _ = _run(box, "read_file_text", {"file_id": "file-1"}, tier=SubscriptionTier.FREE)
    assert "error" in free
    assert "2 MB" in free["error"]

    pro, _ = _run(box, "read_file_text", {"file_id": "file-1"}, tier=SubscriptionTier.PRO)
    assert "text" in pro


def test_the_deployment_ceiling_bounds_the_tier_budget() -> None:
    """A low AI_MAX_DOCUMENT_BYTES wins even for a plan with a bigger allowance."""
    payload = b"x" * (3 * 1024 * 1024)
    box = _toolbox(payload=payload, max_document_bytes=1 * 1024 * 1024)

    pro, _ = _run(box, "read_file_text", {"file_id": "file-1"}, tier=SubscriptionTier.PRO)
    assert "error" in pro


def test_start_conversion_reports_a_file_with_no_known_format() -> None:
    box = _toolbox(files=[_file("file-1", name="README", extension="")])
    result, _ = _run(box, "start_conversion", {"file_id": "file-1", "target_format": "pdf"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Document reading
# ---------------------------------------------------------------------------


def test_read_file_text_returns_the_extracted_text() -> None:
    box = _toolbox(
        extraction=ExtractedDocument(text="Invoice total", truncated=False, supported=True)
    )
    result, artifacts = _run(box, "read_file_text", {"file_id": "file-1"})
    assert result["text"] == "Invoice total"
    assert artifacts[0].type == "file"


def test_read_file_text_truncates_to_max_chars() -> None:
    box = _toolbox(extraction=ExtractedDocument(text="x" * 5000, truncated=False, supported=True))
    result, _ = _run(box, "read_file_text", {"file_id": "file-1", "max_chars": 100})
    assert result["char_count"] == 100
    assert result["truncated"] is True


def test_read_file_text_explains_an_unsupported_format() -> None:
    box = _toolbox(
        extraction=ExtractedDocument(
            text="", truncated=False, supported=False, note="'.zip' is not readable"
        )
    )
    result, _ = _run(box, "read_file_text", {"file_id": "file-1"})
    assert "error" in result
    assert "txt or md" in result["error"]


def test_read_file_text_refuses_a_document_over_the_byte_budget() -> None:
    box = _toolbox(max_document_bytes=10, payload=b"document text that is longer")
    result, _ = _run(box, "read_file_text", {"file_id": "file-1"})
    assert "error" in result
    assert "larger than" in result["error"]


def test_summarize_file_uses_the_model_json() -> None:
    llm = FakeLlmPort(
        [text_response('```json\n{"summary": "A report.", "key_points": ["One"]}\n```')]
    )
    result, _ = _run(_toolbox(llm=llm), "summarize_file", {"file_id": "file-1"})
    assert result == {"summary": "A report.", "key_points": ["One"]}


def test_summarize_file_falls_back_when_the_model_is_unparseable() -> None:
    llm = FakeLlmPort([text_response("I am not JSON at all.")])
    box = _toolbox(
        llm=llm,
        extraction=ExtractedDocument(
            text="First sentence here. Second one too. Third one. Longest line of all\nshort",
            truncated=False,
            supported=True,
        ),
    )
    result, _ = _run(box, "summarize_file", {"file_id": "file-1"})
    assert result["extractive"] is True
    assert "First sentence here" in result["summary"]
    assert result["key_points"]


def test_summarize_file_is_extractive_when_the_backend_is_echo() -> None:
    llm = FakeLlmPort(model="echo")
    box = _toolbox(
        llm=llm,
        extraction=ExtractedDocument(text="Alpha beta. Gamma delta.", truncated=False, supported=True),
    )
    result, _ = _run(box, "summarize_file", {"file_id": "file-1"})
    assert result["extractive"] is True
    # No model call was made at all — the offline backend cannot summarise.
    assert llm.calls == []


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def test_move_file_accepts_a_folder_name() -> None:
    box = _toolbox(files=[_file("file-1")], folders=[_folder("folder-1", "Archive")])
    result, artifacts = _run(box, "move_file", {"file_id": "file-1", "folder": "archive"})
    assert "error" not in result
    assert artifacts[0].meta["folder_id"] == "folder-1"


def test_create_folder_requires_a_name() -> None:
    result, _ = _run(_toolbox(), "create_folder", {})
    assert "error" in result


def test_create_folder_emits_a_folder_artifact() -> None:
    """The UI needs a folder artifact to offer a link to the new folder."""
    box = _toolbox(folders=[_folder("folder-parent", "Projects")])
    result, artifacts = _run(box, "create_folder", {"name": "Invoices", "parent": "Projects"})
    assert "error" not in result
    assert result["folder_id"] == "folder-new"
    assert result["parent_id"] == "folder-parent"
    assert artifacts == [
        Artifact(
            type="folder",
            id="folder-new",
            name="Invoices",
            meta={"parent_id": "folder-parent"},
        )
    ]


def test_create_folder_artifact_at_root_has_a_null_parent() -> None:
    _, artifacts = _run(_toolbox(), "create_folder", {"name": "Loose"})
    assert artifacts[0].type == "folder"
    assert artifacts[0].meta == {"parent_id": None}


def test_move_file_emits_only_the_file_artifact_with_the_new_folder() -> None:
    """The destination folder is NOT emitted; the file's meta carries it."""
    box = _toolbox(files=[_file("file-1")], folders=[_folder("folder-1", "Archive")])
    _, artifacts = _run(box, "move_file", {"file_id": "file-1", "folder": "archive"})
    assert [artifact.type for artifact in artifacts] == ["file"]
    assert artifacts[0].meta["folder_id"] == "folder-1"


# ---------------------------------------------------------------------------
# Deletion proposals (never deletions)
# ---------------------------------------------------------------------------
#
# The security model of the whole feature: the model can only *propose* a
# deletion, and the removal happens later from an authenticated confirm click.
# If any test here fails, a single prompt can remove user data with no human in
# the loop.


def test_delete_file_never_deletes_anything() -> None:
    box = _toolbox(files=[_file("file-1")])
    result, _ = _run(
        box,
        "delete_file",
        {"file_id": "file-1", "reason": "user asked"},
        conversation_id="conv-1",
    )
    service = box._files
    assert isinstance(service, FakeFileService)
    assert service.deleted == []
    assert "file-1" in service.files
    assert result["status"] == "awaiting_confirmation"


def test_delete_file_emits_the_documented_artifact() -> None:
    box = _toolbox(files=[_file("file-1", name="report.pdf", folder_id="folder-1")])
    result, artifacts = _run(
        box, "delete_file", {"file_id": "file-1"}, conversation_id="conv-9"
    )
    assert result == {
        "status": "awaiting_confirmation",
        "file_id": "file-1",
        "file_name": "report.pdf",
        "extension": "pdf",
        "size_bytes": 1024,
        "folder_id": "folder-1",
    }
    # Exactly the frozen cross-stack shape, conversation_id included: the
    # components that render the chip (the floating mini chat) never learned the
    # conversation id any other way.
    assert artifacts == [
        Artifact(
            type="delete",
            id="file-1",
            name="report.pdf",
            meta={
                "state": "pending",
                "conversation_id": "conv-9",
                "extension": "pdf",
                "size_bytes": 1024,
                "folder_id": "folder-1",
            },
        )
    ]


def test_delete_file_without_a_conversation_still_emits_a_null_id() -> None:
    """A direct ``execute`` call (the unit suite) has no conversation id."""
    _, artifacts = _run(_toolbox(), "delete_file", {"file_id": "file-1"})
    assert artifacts[0].meta["conversation_id"] is None


def test_delete_file_rejects_another_users_file() -> None:
    box = _toolbox(files=[_file("file-1", user_id=OTHER)])
    result, artifacts = _run(
        box, "delete_file", {"file_id": "file-1"}, conversation_id="conv-1"
    )
    assert "error" in result
    assert artifacts == []


def test_delete_file_rejects_an_unknown_file() -> None:
    result, artifacts = _run(_toolbox(), "delete_file", {"file_id": "nope"})
    assert "error" in result
    assert artifacts == []


def test_delete_file_requires_a_file_id() -> None:
    result, artifacts = _run(_toolbox(), "delete_file", {})
    assert "error" in result
    assert artifacts == []


def test_delete_file_budget_counts_started_conversions() -> None:
    """One instruction must not be able to mix conversions and deletions freely."""
    box = _toolbox(files=[_file("file-1")])
    spent = [
        Artifact(type="job", id="job-1", name="x.pdf", meta={"started": True})
    ]
    result = asyncio.run(
        box.execute(
            "delete_file",
            {"file_id": "file-1"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=spent,
            conversation_id="conv-1",
        )
    )
    assert "error" in result
    assert "at most 1" in result["error"]


def test_delete_file_budget_counts_pending_deletions() -> None:
    box = _toolbox(files=[_file("file-1"), _file("file-2", key="objects/2.pdf")])
    spent = [Artifact(type="delete", id="file-2", name="b.pdf", meta={"state": "pending"})]
    result = asyncio.run(
        box.execute(
            "delete_file",
            {"file_id": "file-1"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=spent,
            conversation_id="conv-1",
        )
    )
    assert "error" in result
    assert "at most 1" in result["error"]


def test_read_only_artifacts_do_not_consume_the_delete_budget() -> None:
    """A listed file or a reported job is not an action, so it costs nothing."""
    box = _toolbox(files=[_file("file-1")])
    reported = [
        Artifact(type="file", id=f"f-{index}", name="x.pdf", meta={"extension": "pdf"})
        for index in range(max_actions_per_turn(SubscriptionTier.FREE) * 2)
    ]
    result = asyncio.run(
        box.execute(
            "delete_file",
            {"file_id": "file-1"},
            user_id=OWNER,
            tier=SubscriptionTier.FREE,
            artifacts=reported,
            conversation_id="conv-1",
        )
    )
    assert "error" not in result


def test_delete_file_describe_never_claims_a_deletion() -> None:
    result, _ = _run(
        _toolbox(), "delete_file", {"file_id": "file-1"}, conversation_id="conv-1"
    )
    summary = AssistantToolBox.describe("delete_file", result)
    assert "report.pdf" in summary
    assert "deleted" not in summary.lower()
    assert "confirm" in summary.lower()


def test_delete_file_spec_is_closed_and_takes_a_reason() -> None:
    spec = next(spec for spec in _toolbox().specs() if spec.name == "delete_file")
    assert spec.parameters["additionalProperties"] is False
    assert spec.parameters["required"] == ["file_id"]
    assert set(spec.parameters["properties"]) == {"file_id", "reason"}


# ---------------------------------------------------------------------------
# Conversion search
# ---------------------------------------------------------------------------


def test_list_recent_conversions_passes_query_and_format_through() -> None:
    conversions = FakeConversionService()
    box = _toolbox(conversion_service=conversions)
    result, _ = _run(box, "list_recent_conversions", {"query": "homework", "format": "PDF"})
    assert "error" not in result
    assert conversions.search_calls == [
        {"user_id": OWNER, "query": "homework", "fmt": "PDF", "limit": 10}
    ]
    assert result["query"] == "homework"
    assert result["format"] == "PDF"


def test_list_recent_conversions_describe_reflects_a_filtered_search() -> None:
    box = _toolbox()
    filtered, _ = _run(box, "list_recent_conversions", {"query": "homework"})
    assert box.describe("list_recent_conversions", filtered) == (
        "Found 0 conversion(s) matching your search"
    )
    unfiltered, _ = _run(box, "list_recent_conversions", {})
    assert box.describe("list_recent_conversions", unfiltered) == "Found 0 conversion(s)"


def test_list_recent_conversions_filters_are_in_the_schema() -> None:
    spec = next(
        spec for spec in _toolbox().specs() if spec.name == "list_recent_conversions"
    )
    assert spec.parameters["additionalProperties"] is False
    assert spec.parameters["properties"]["query"]["type"] == ["string", "null"]
    assert spec.parameters["properties"]["format"]["type"] == ["string", "null"]


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


def test_describe_reports_tool_errors_verbatim() -> None:
    assert _toolbox().describe("list_files", {"error": "nope"}) == "nope"


def test_describe_never_raises_for_unknown_tools() -> None:
    assert _toolbox().describe("something_new", {}) == "Done"


def test_format_label_and_category_fall_back_gracefully() -> None:
    assert format_label("pdf") == "PDF document"
    assert format_label("xyz") == "XYZ"
    assert format_category("docx") == "document"
    assert format_category("xyz") == "file"


def test_max_list_limit_is_wired_into_the_schema() -> None:
    spec = next(spec for spec in _toolbox().specs() if spec.name == "list_files")
    assert spec.parameters["properties"]["limit"]["maximum"] == _MAX_LIST_LIMIT


# ---------------------------------------------------------------------------
# Account overview
# ---------------------------------------------------------------------------


def test_account_overview_tool_is_registered_with_a_schema() -> None:
    spec = next(
        spec for spec in _toolbox().specs() if spec.name == "get_account_overview"
    )
    assert spec.parameters["type"] == "object"
    assert spec.parameters["additionalProperties"] is False
    assert set(spec.parameters["properties"]) == {"range", "format", "status"}
    assert spec.parameters["properties"]["range"]["enum"] == [
        "24h",
        "7d",
        "30d",
        "all",
        None,
    ]
    assert _toolbox().label_for("get_account_overview") != "Working on it"


def test_account_overview_maps_range_to_a_lookback_window() -> None:
    box = _toolbox()
    account = box._accounts
    assert isinstance(account, FakeAccountPort)

    _run(
        box,
        "get_account_overview",
        {"range": "7d", "format": ".PDF", "status": "completed"},
    )
    call = account.calls[0]
    assert call["user_id"] == OWNER
    assert call["fmt"] == "pdf"
    assert call["status"] == "COMPLETED"
    assert call["since"] is not None
    drift = timedelta(days=7) - (datetime.now(UTC) - call["since"])
    assert abs(drift) < timedelta(minutes=1)


def test_account_overview_defaults_to_all_time_and_no_filter() -> None:
    box = _toolbox()
    account = box._accounts
    assert isinstance(account, FakeAccountPort)

    result, _ = _run(box, "get_account_overview", {})
    assert account.calls[0]["since"] is None
    assert account.calls[0]["fmt"] is None
    assert account.calls[0]["status"] is None
    assert result["range"] == "all"


def test_account_overview_output_shape() -> None:
    result, _ = _run(_toolbox(), "get_account_overview", {"range": "24h"})
    assert set(result) == {
        "tier",
        "credits_remaining",
        "credits_reset_at",
        "storage_used_bytes",
        "storage_limit_bytes",
        "jobs",
        "by_target_format",
        "range",
        "filter",
    }
    assert result["tier"] == "FREE"
    assert result["credits_remaining"] == 42
    assert result["credits_reset_at"] == "2026-10-01T00:00:00+00:00"
    assert result["storage_used_bytes"] == 1024
    assert result["storage_limit_bytes"] == 5 * 1024**3
    assert result["jobs"] == {"total": 4, "completed": 2, "failed": 1, "active": 1}
    assert result["by_target_format"] == [
        {"target_format": "pdf", "count": 3},
        {"target_format": "txt", "count": 1},
    ]
    assert result["range"] == "24h"
    assert result["filter"] == {"format": None, "status": None}


def test_account_overview_is_read_only_and_appends_no_artifacts() -> None:
    result, artifacts = _run(_toolbox(), "get_account_overview", {})
    assert "error" not in result
    assert artifacts == []


def test_account_overview_describe_reports_the_numbers() -> None:
    box = _toolbox()
    result, _ = _run(box, "get_account_overview", {"range": "24h"})
    summary = box.describe("get_account_overview", result)
    assert "4" in summary
    assert "24h" in summary


def test_account_overview_describe_mentions_credits_when_there_are_no_jobs() -> None:
    empty = AccountOverview(
        tier="FREE",
        credits_remaining=7,
        credits_reset_at=None,
        storage_used_bytes=0,
        storage_limit_bytes=5 * 1024**3,
        jobs_total=0,
        jobs_completed=0,
        jobs_failed=0,
        jobs_active=0,
        by_target_format=(),
    )
    box = _toolbox(account_port=FakeAccountPort(empty))
    result, _ = _run(box, "get_account_overview", {})
    summary = box.describe("get_account_overview", result)
    assert "7" in summary
    assert "credit" in summary
