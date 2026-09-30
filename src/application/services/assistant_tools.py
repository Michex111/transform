"""The assistant's tools: the only way it can learn about or act on user data.

WHY a toolbox and not prompt context: the model has no access to the database,
so every fact it states about the user's files has to arrive through a function
call. Concentrating those functions here (rather than in the service) means the
ownership rules are stated once, in one place, next to the JSON schemas that
describe them — and the whole surface is unit-testable without an LLM.

Two invariants hold for every tool:

* **Ownership is delegated, never re-implemented.** Files and jobs are fetched
  through ``FileService`` / ``ConversionService``, which already refuse rows the
  caller does not own. A tool cannot be the weak link that lets the model read
  someone else's document.
* **Failures are values, not exceptions.** A tool returns
  ``{"error": "<human sentence>"}`` so the agent loop can hand the sentence back
  to the model and let it recover. Raising here would abort the turn on
  something as ordinary as a mistyped file name.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from src.application.dtos.assistant_dto import Artifact
from src.application.exceptions.file_system_exceptions import FileRecordNotFoundError
from src.application.ports.assistant_account_port import AssistantAccountPort
from src.application.ports.assistant_model_port import AssistantModelResolver
from src.application.ports.document_text_port import (
    DocumentTextExtractorPort,
    ExtractedDocument,
)
from src.application.ports.llm_port import LlmMessage, LlmToolSpec
from src.application.services.assistant_prompts import SUMMARY_PROMPT
from src.application.services.file_listing import (
    DEFAULT_FILE_SORT,
    DEFAULT_FILE_SORT_ORDER,
    FileSortKey,
    FileSortOrder,
    parse_file_sort,
    parse_file_sort_order,
)
from src.domain.assistant.exceptions.assistant_exceptions import (
    AssistantAttachmentNotFound,
)
from src.domain.assistant.policies.assistant_policy import (
    max_actions_per_turn,
    max_document_bytes_for_tier,
)
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.storage.sanitize import extension_from_filename
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.infrastructure.database.models import UserFileModel, UserFolderModel
from src.infrastructure.logging.audit import log_permission_denied

logger = logging.getLogger(__name__)

#: Upper bound a single tool will ever return, regardless of what the model
#: asked for. The model's ``limit`` argument is untrusted (it comes from a JSON
#: blob the model wrote), and an unbounded list is a token bomb: 10 000 rows
#: would overflow the context window and the provider bill.
_MAX_LIST_LIMIT = 50
#: How many files a single listing tool scans when filtering by a query. The
#: repository has no name search, so filtering happens here.
_QUERY_SCAN_LIMIT = 200
#: Artifacts emitted per tool call. A "list everything" turn must not render
#: hundreds of chips in the UI.
_MAX_ARTIFACTS_PER_TOOL = 5
#: Characters of a document handed back by ``read_file_text`` by default.
_DEFAULT_READ_CHARS = 8000

#: Relative windows the account tool accepts, mapped to their look-back span.
#: ``"all"`` (and an absent value) is intentionally not listed: it means "no
#: time bound", which is expressed as ``None`` rather than a window.
_RANGE_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _since_for_range(value: str | None) -> datetime | None:
    """Translate the account tool's ``range`` argument into a look-back instant.

    Computed from the current UTC time rather than passed in, because the model
    can only express a *relative* range and the server — not the model — must
    own what "24h" resolves to. An unknown value degrades to "no bound", which
    is the safe reading (it widens the query rather than silently narrowing it).
    """
    if value is None:
        return None
    window = _RANGE_WINDOWS.get(value.strip().lower())
    if window is None:
        return None
    return datetime.now(UTC) - window


def _human_size(size_bytes: int) -> str:
    """Compact, human-readable byte size for the attachment context sentence.

    Presentation only: the result is placed into a prompt ("27 KB") and is
    never compared or used as a limit, so rounding cannot affect enforcement.
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


@dataclass(frozen=True)
class AttachmentRef:
    """A file attached to one chat message, resolved and ownership-checked.

    Carries only what the prompt needs — name, id, format, size — and never the
    object key or any other storage detail, so an attachment cannot become a way
    to disclose where a file physically lives.
    """

    file_id: str
    file_name: str
    extension: str
    size_bytes: int


#: Longest page-context hint embedded in the prompt. The API schema caps it at
#: the same value; the slice here is the defence in case the service is called
#: from somewhere other than that route.
_MAX_CONTEXT_CHARS = 200


def build_attachment_context(
    attachments: Sequence[AttachmentRef], context: str | None
) -> str:
    """Build the extra system line(s) describing this turn's attachments/page.

    Two deliberate properties: the surrounding sentence is *ours* (the model is
    told what the ids are for, rather than handed a bare blob), and ``context``
    is truncated and only ever embedded as text — never used to build a path or
    a query — so a hostile page value cannot escape the prompt.
    """
    lines: list[str] = []
    if attachments:
        lines.append(
            "The user attached these files to this message. Use these file ids "
            "with your tools:"
        )
        lines.extend(
            f"- {item.file_name} (file_id: {item.file_id}, "
            f"format: {item.extension or 'unknown'}, {_human_size(item.size_bytes)})"
            for item in attachments
        )
    cleaned = (context or "").strip()
    if cleaned:
        lines.append(
            f"The user is currently viewing the page: {cleaned[:_MAX_CONTEXT_CHARS]}."
        )
    return "\n".join(lines)


#: Friendly description of a file format, for the ``label`` on a recommendation.
_FORMAT_LABELS: dict[str, str] = {
    "pdf": "PDF document",
    "doc": "Word document (legacy)",
    "docx": "Word document",
    "odt": "OpenDocument text",
    "rtf": "Rich Text",
    "txt": "Plain text",
    "md": "Markdown",
    "html": "Web page",
    "epub": "EPUB e-book",
    "mobi": "Kindle (legacy)",
    "azw3": "Kindle e-book",
    "fb2": "FictionBook",
    "lit": "Microsoft Reader",
    "tex": "LaTeX source",
    "xls": "Excel workbook (legacy)",
    "xlsx": "Excel workbook",
    "ods": "OpenDocument spreadsheet",
    "csv": "CSV table",
    "ppt": "PowerPoint deck (legacy)",
    "pptx": "PowerPoint deck",
    "odp": "OpenDocument presentation",
    "jpg": "JPEG image",
    "jpeg": "JPEG image",
    "png": "PNG image",
    "gif": "GIF image",
    "bmp": "Bitmap image",
    "tiff": "TIFF image",
    "webp": "WebP image",
    "avif": "AVIF image",
    "svg": "SVG vector image",
    "ico": "Icon file",
    "mp3": "MP3 audio",
    "wav": "WAV audio",
    "flac": "FLAC audio",
    "aac": "AAC audio",
    "ogg": "Ogg audio",
    "opus": "Opus audio",
    "m4a": "M4A audio",
    "wma": "WMA audio",
    "mp4": "MP4 video",
    "mkv": "Matroska video",
    "mov": "QuickTime video",
    "avi": "AVI video",
    "webm": "WebM video",
    "wmv": "WMV video",
    "zip": "ZIP archive",
    "tar": "TAR archive",
    "gz": "Gzip archive",
    "7z": "7-Zip archive",
    "ttf": "TrueType font",
    "otf": "OpenType font",
    "woff": "WOFF font",
    "woff2": "WOFF2 font",
    "eot": "Embedded OpenType font",
}

#: Coarse group used to pick a sensible icon in the UI. Anything unknown is
#: ``"file"`` — a wrong-but-harmless fallback, unlike a wrong label.
_FORMAT_CATEGORIES: dict[str, str] = {
    **{fmt: "document" for fmt in ("pdf", "doc", "docx", "odt", "rtf", "txt", "md", "html", "tex")},
    **{fmt: "ebook" for fmt in ("epub", "mobi", "azw", "azw3", "fb2", "lit")},
    **{fmt: "spreadsheet" for fmt in ("xls", "xlsx", "ods", "csv", "tsv")},
    **{fmt: "presentation" for fmt in ("ppt", "pptx", "odp")},
    **{fmt: "image" for fmt in ("jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp", "avif", "svg", "ico")},
    **{fmt: "audio" for fmt in ("mp3", "wav", "flac", "aac", "ogg", "opus", "m4a", "wma", "aiff", "alac", "m4b")},
    **{fmt: "video" for fmt in ("mp4", "mkv", "mov", "avi", "webm", "wmv", "flv", "m4v", "mpg", "3gp")},
    **{fmt: "archive" for fmt in ("zip", "tar", "gz", "bz2", "xz", "lzma", "tar.gz", "tar.bz2", "tar.xz")},
    **{fmt: "font" for fmt in ("ttf", "otf", "woff", "woff2", "eot")},
}

#: Human phrasing shown while (and after) each tool runs. Holding the label next
#: to the tool means the SSE vocabulary cannot drift from the tool list.
_TOOL_LABELS: dict[str, str] = {
    "list_files": "Looking through your files",
    "list_folders": "Looking through your folders",
    "get_file_info": "Checking that file",
    "list_supported_targets": "Checking the supported formats",
    "read_file_text": "Reading the document",
    "summarize_file": "Summarising the document",
    "start_conversion": "Starting the conversion",
    "get_conversion_status": "Checking the conversion",
    "list_recent_conversions": "Looking at your recent conversions",
    "create_folder": "Creating the folder",
    "move_file": "Moving the file",
    "delete_file": "Preparing your confirmation",
    "get_account_overview": "Checking your account usage",
}


def format_label(fmt: str) -> str:
    """Human name of a file format (falls back to the uppercased extension)."""
    return _FORMAT_LABELS.get(fmt, fmt.upper())


def format_category(fmt: str) -> str:
    """Coarse family of a file format (``"file"`` when unknown)."""
    return _FORMAT_CATEGORIES.get(fmt, "file")


@runtime_checkable
class AssistantDocumentStorage(Protocol):
    """The slice of object storage the document tools need.

    Narrower than the full storage adapter on purpose: the assistant only ever
    needs to know how big an object is and to read its bytes, so it cannot
    accidentally grow the ability to delete or overwrite the user's data.
    """

    async def stat_object(self, object_key: str) -> dict[str, Any] | None:
        """Metadata for ``object_key`` (``size`` in particular), or ``None``."""
        ...

    async def read_object_head(self, object_key: str, max_bytes: int = 4096) -> bytes:
        """Read up to ``max_bytes`` of ``object_key``."""
        ...


@runtime_checkable
class AssistantFileServicePort(Protocol):
    """The file operations the tools use, all of them ownership-checked.

    Declared as a port rather than depending on the concrete ``FileService`` so
    the tool surface the assistant may touch is readable in one place — and so a
    test (or a future file backend) can supply it without FileService's upload,
    quota and crypto machinery. Every implementation must enforce the same
    ownership rule: a foreign id is a ``FileRecordNotFoundError``, never a row.
    """

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel:
        ...

    async def list_files(
        self, user_id: int, folder_id: str | None = None, *, offset: int = 0, limit: int = 20,
        sort: FileSortKey = DEFAULT_FILE_SORT,
        order: FileSortOrder = DEFAULT_FILE_SORT_ORDER,
    ) -> tuple[list[UserFileModel], int]:
        ...

    async def list_all_files(
        self, user_id: int, *, offset: int = 0, limit: int = 20,
        sort: FileSortKey = DEFAULT_FILE_SORT,
        order: FileSortOrder = DEFAULT_FILE_SORT_ORDER,
    ) -> tuple[list[UserFileModel], int]:
        """Every file the user owns, in any folder, ordered by the database."""
        ...

    async def search_files(
        self, user_id: int, query: str, *, offset: int = 0, limit: int = 50,
        sort: FileSortKey = DEFAULT_FILE_SORT,
        order: FileSortOrder = DEFAULT_FILE_SORT_ORDER,
    ) -> tuple[list[UserFileModel], int]:
        ...

    async def create_folder(
        self, user_id: int, name: str, parent_id: str | None = None
    ) -> UserFolderModel:
        ...

    async def move_file(
        self, user_id: int, file_id: str, folder_id: str | None
    ) -> UserFileModel:
        ...

    async def delete_file(self, user_id: int, file_id: str) -> None:
        """Delete an owned file (object + record). Foreign/missing is an error.

        Declared on the port so the toolbox can offer the one deletion path a
        *user confirmation* is allowed to reach (``delete_owned_file``), while
        remaining unable to expose it to the model — no tool ever calls it.
        """
        ...


@runtime_checkable
class AssistantConversionServicePort(Protocol):
    """The conversion operations the tools use, all of them ownership-checked."""

    async def convert_library_file(
        self,
        *,
        file_name: str,
        source_format: str,
        target_format: str,
        object_key: str,
        user_id: int,
        tier: SubscriptionTier = SubscriptionTier.FREE,
    ) -> ConversionJob:
        ...

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None:
        ...

    async def list_history(
        self, user_id: int, *, offset: int = 0, limit: int = 20, since: datetime | None = None
    ) -> tuple[list[ConversionJob], int]:
        ...

    async def search_jobs(
        self, user_id: int, *, query: str | None = None, fmt: str | None = None, limit: int = 10
    ) -> list[ConversionJob]:
        ...


@runtime_checkable
class FolderLookupPort(Protocol):
    """The folder reads the tools need (their writes go through the file service)."""

    async def get_by_id(self, folder_id: str) -> UserFolderModel | None:
        ...

    async def list_by_parent(
        self, user_id: int, parent_id: str | None, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[UserFolderModel], int]:
        ...


def _actions_taken(artifacts: Sequence[Artifact]) -> int:
    """Count the destructive actions this turn has already accumulated.

    Two artifact kinds consume the same per-turn budget, because they are the
    same kind of risk — something the model made happen to real user data:

    * a conversion the model *started* (``meta["started"] is True``);
    * a deletion the model *proposed* (a ``delete`` artifact left ``pending``).

    Read-only tools' artifacts (a file it merely listed, a job it merely
    reported) carry neither marker and cost nothing. Counting from the caller's
    own accumulator means the limit needs no per-turn state: the list of
    artifacts IS the record of what this turn has done.

    Both conditions name the artifact ``type`` as well as the marker key. The
    markers alone would be enough today, but ``state`` is a generic-sounding key
    that a future artifact kind could plausibly carry, and a read-only artifact
    silently consuming the destructive budget would show up as the assistant
    refusing work for no reason the user can see.
    """
    return sum(
        1
        for artifact in artifacts
        if (artifact.type == "job" and artifact.meta.get("started") is True)
        or (artifact.type == "delete" and artifact.meta.get("state") == "pending")
    )


def _tool_schema(
    properties: dict[str, Any], required: Sequence[str] = ()
) -> dict[str, Any]:
    """Build a JSON schema for a tool's arguments.

    ``additionalProperties: false`` on every tool is not cosmetic: it stops the
    model from inventing an argument (``{"force": true}``) that the caller would
    silently ignore, which reads to the user as the assistant not listening.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _file_payload(row: UserFileModel) -> dict[str, Any]:
    """JSON-safe description of a file row, as handed to the model."""
    return {
        "file_id": row.id,
        "file_name": row.file_name,
        "extension": row.file_extension,
        "size_bytes": row.file_size_bytes,
        "folder_id": row.folder_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _file_artifact(row: UserFileModel) -> Artifact:
    """Clickable artifact for a file the assistant touched."""
    return Artifact(
        type="file",
        id=row.id,
        name=row.file_name,
        meta={"extension": row.file_extension, "folder_id": row.folder_id},
    )


def _folder_artifact(row: UserFolderModel) -> Artifact:
    """Clickable artifact for a folder the assistant created.

    A folder has no meaningful ``extension``, so the UI turns this straight into
    a link to that folder in the drive (it navigates to ``id`` and shows
    ``name``); ``parent_id`` is carried so the chip can also say where the new
    folder lives. Without it the user is told "I created the folder" with
    nothing to click, and has to hunt for it.
    """
    return Artifact(
        type="folder",
        id=row.id,
        name=row.name,
        meta={"parent_id": row.parent_id},
    )


def _format_list(items: list[str], limit: int = 12) -> str:
    """Readable, bounded enumeration for an error message."""
    shown = ", ".join(items[:limit])
    if len(items) > limit:
        shown += f", … ({len(items) - limit} more)"
    return shown


class AssistantToolBox:
    """Executes the assistant's tools against the file and conversion services."""

    def __init__(
        self,
        *,
        file_service: AssistantFileServicePort,
        conversion_service: AssistantConversionServicePort,
        folder_repository: FolderLookupPort,
        storage: AssistantDocumentStorage,
        extractor: DocumentTextExtractorPort,
        models: AssistantModelResolver,
        account_port: AssistantAccountPort,
        max_document_bytes: int,
        summary_max_input_chars: int,
    ) -> None:
        self._files = file_service
        self._conversions = conversion_service
        self._folders = folder_repository
        self._storage = storage
        self._extractor = extractor
        #: The tier's model transport is resolved per call from the caller's
        #: tier (not stored): the toolbox is a shared singleton, so holding a
        #: per-request model here would leak one user's plan into another's turn.
        self._models = models
        self._accounts = account_port
        #: Deployment ceiling on a document read. The effective budget is the
        #: MINIMUM of this and the tier's entitlement (see ``read_document``),
        #: so an operator can lower the ceiling and a plan can stay cheaper.
        self._max_document_bytes = max_document_bytes
        self._summary_max_input_chars = summary_max_input_chars

    async def resolve_attachments(
        self, user_id: int, file_ids: Sequence[str]
    ) -> list[AttachmentRef]:
        """Resolve the files a user attached to a message, enforcing ownership.

        Delegated to ``FileService.get_file`` so a foreign or unknown id cannot
        be attached: the same rule that protects every other read also protects
        what the model is told about. A bad id raises
        :class:`AssistantAttachmentNotFound` (the router maps it to a 404)
        instead of being silently dropped, because a client that sent a file it
        does not own must be told its message was *not* processed with it.
        """
        resolved: list[AttachmentRef] = []
        for file_id in file_ids:
            try:
                row = await self._files.get_file(user_id, file_id)
            except FileRecordNotFoundError as exc:
                raise AssistantAttachmentNotFound("Attachment not found") from exc
            resolved.append(
                AttachmentRef(
                    file_id=row.id,
                    file_name=row.file_name,
                    extension=row.file_extension,
                    size_bytes=row.file_size_bytes,
                )
            )
        return resolved

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def specs(self) -> list[LlmToolSpec]:
        """Every tool, described for the model."""
        return [
            LlmToolSpec(
                name="list_files",
                description=(
                    "List or search the user's files. A `query` searches the "
                    "WHOLE drive (every folder) for a case-insensitive file-name "
                    "substring, so use it for 'find my invoice'. Passing `folder` "
                    "narrows the listing — and any `query` — to that one folder. "
                    "`all_folders: true` lists the whole drive with no name "
                    "filter, which is what a question about the drive as a whole "
                    "needs. `sort` + `order` + a small `limit` answer superlatives "
                    "in one call — for 'what is my largest file?' use "
                    "{all_folders: true, sort: 'size', order: 'desc', limit: 1} "
                    "and report ONLY the file that comes back. Use this to "
                    "resolve a file the user referred to by name."
                ),
                parameters=_tool_schema(
                    {
                        "folder": {
                            "type": ["string", "null"],
                            "description": (
                                "Folder name or id to narrow the listing to; omit "
                                "to search the whole drive (with a query) or list "
                                "the root (without one)."
                            ),
                        },
                        "query": {
                            "type": ["string", "null"],
                            "description": (
                                "Case-insensitive file-name substring; searches "
                                "across all folders when no folder is given."
                            ),
                        },
                        "all_folders": {
                            "type": ["boolean"],
                            "description": (
                                "List every folder, not just the root. Use for "
                                "questions about the drive as a whole ('my largest "
                                "file'). Without it, a listing with no folder and "
                                "no query returns root-level files only."
                            ),
                        },
                        "sort": {
                            "type": ["string", "null"],
                            "enum": [key.value for key in FileSortKey],
                            "description": (
                                "Order the results by 'name', 'size' or 'date' "
                                "(the default). Applied by the server before "
                                "`limit`, so 'size' really does return the "
                                "largest/smallest files."
                            ),
                        },
                        "order": {
                            "type": ["string", "null"],
                            "enum": [value.value for value in FileSortOrder],
                            "description": (
                                "'desc' (the default: largest, newest, Z-first) "
                                "or 'asc' (smallest, oldest, A-first)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_LIST_LIMIT,
                            "description": (
                                "Maximum files to return (default 20). Use a small "
                                "value for a superlative — 1 for 'the largest'."
                            ),
                        },
                    }
                ),
            ),
            LlmToolSpec(
                name="list_folders",
                description=(
                    "List the folders inside a folder, or the root folders when "
                    "no parent is given."
                ),
                parameters=_tool_schema(
                    {
                        "parent": {
                            "type": ["string", "null"],
                            "description": "Parent folder name or id; omit for the root.",
                        }
                    }
                ),
            ),
            LlmToolSpec(
                name="get_file_info",
                description="Get the details of one file by its id.",
                parameters=_tool_schema(
                    {"file_id": {"type": "string", "description": "The file's id."}},
                    required=["file_id"],
                ),
            ),
            LlmToolSpec(
                name="list_supported_targets",
                description=(
                    "List the target formats this service can convert a given "
                    "source format to. Always call this before offering or "
                    "starting a conversion."
                ),
                parameters=_tool_schema(
                    {
                        "source_format": {
                            "type": "string",
                            "description": "Source extension without a dot, e.g. 'pdf'.",
                        }
                    },
                    required=["source_format"],
                ),
            ),
            LlmToolSpec(
                name="read_file_text",
                description=(
                    "Read the extracted text of a document (txt/md/csv, PDF, "
                    "Word, Excel, PowerPoint). Returns the text, possibly "
                    "truncated."
                ),
                parameters=_tool_schema(
                    {
                        "file_id": {"type": "string"},
                        "max_chars": {
                            "type": ["integer", "null"],
                            "minimum": 1,
                            "description": "Maximum characters to return.",
                        },
                    },
                    required=["file_id"],
                ),
            ),
            LlmToolSpec(
                name="summarize_file",
                description=(
                    "Summarise one document and list its key points. Use this "
                    "whenever the user asks what a document is about."
                ),
                parameters=_tool_schema(
                    {"file_id": {"type": "string"}}, required=["file_id"]
                ),
            ),
            LlmToolSpec(
                name="start_conversion",
                description=(
                    "Start converting one of the user's files to another format. "
                    "Only call this after list_supported_targets confirmed the "
                    "target, and only once per file the user asked for. The "
                    "converted file is produced as a downloadable output; it is "
                    "NOT filed into a drive folder automatically, so never "
                    "promise to put the result in a folder."
                ),
                parameters=_tool_schema(
                    {
                        "file_id": {"type": "string"},
                        "target_format": {
                            "type": "string",
                            "description": "Target extension without a dot.",
                        },
                    },
                    required=["file_id", "target_format"],
                ),
            ),
            LlmToolSpec(
                name="get_conversion_status",
                description="Check the status of a conversion job by its id.",
                parameters=_tool_schema(
                    {"job_id": {"type": "string"}}, required=["job_id"]
                ),
            ),
            LlmToolSpec(
                name="list_recent_conversions",
                description=(
                    "List or search the user's conversions. With no filters it "
                    "lists the most recent ones; a `query` matches the input or "
                    "output file name (case-insensitive substring) and a `format` "
                    "matches jobs made from OR into that format. Use the filters "
                    "for a conversion the user describes rather than names, e.g. "
                    "'the homework one'."
                ),
                parameters=_tool_schema(
                    {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _MAX_LIST_LIMIT,
                            "description": "Maximum jobs to return (default 10).",
                        },
                        "query": {
                            "type": ["string", "null"],
                            "description": (
                                "Case-insensitive substring of the input or "
                                "output file name."
                            ),
                        },
                        "format": {
                            "type": ["string", "null"],
                            "description": (
                                "Source OR target format, without a dot (e.g. "
                                "'pdf')."
                            ),
                        },
                    }
                ),
            ),
            LlmToolSpec(
                name="create_folder",
                description="Create a folder, optionally inside another folder.",
                parameters=_tool_schema(
                    {
                        "name": {"type": "string", "description": "New folder name."},
                        "parent": {
                            "type": ["string", "null"],
                            "description": "Parent folder name or id; omit for the root.",
                        },
                    },
                    required=["name"],
                ),
            ),
            LlmToolSpec(
                name="move_file",
                description="Move one of the user's files into a folder (or to the root).",
                parameters=_tool_schema(
                    {
                        "file_id": {"type": "string"},
                        "folder": {
                            "type": ["string", "null"],
                            "description": "Destination folder name or id; null for the root.",
                        },
                    },
                    required=["file_id"],
                ),
            ),
            LlmToolSpec(
                name="delete_file",
                description=(
                    "Propose deleting one of the user's files. This does NOT "
                    "delete anything: it asks the user to confirm the deletion "
                    "in the app, and the file is only removed if they click to "
                    "confirm. Call it at most once per file the user asked to "
                    "remove, then tell the user you need their confirmation. "
                    "NEVER say or imply a file has been deleted — you cannot "
                    "delete files yourself."
                ),
                parameters=_tool_schema(
                    {
                        "file_id": {"type": "string", "description": "The file's id."},
                        "reason": {
                            "type": ["string", "null"],
                            "description": (
                                "Short reason for the deletion, to show the user "
                                "why you are asking."
                            ),
                        },
                    },
                    required=["file_id"],
                ),
            ),
            LlmToolSpec(
                name="get_account_overview",
                description=(
                    "Read the user's own account usage: remaining credits, storage, "
                    "and how many conversions they have run, broken down by status "
                    "and target format. ALWAYS use this for any question about "
                    "credits, remaining usage, storage, or how many/what kind of "
                    "conversions were made, and report its exact numbers — never "
                    "estimate them."
                ),
                parameters=_tool_schema(
                    {
                        "range": {
                            "type": ["string", "null"],
                            "enum": ["24h", "7d", "30d", "all", None],
                            "description": (
                                "Time window for the job counts. 'all' or null "
                                "means no time limit (default)."
                            ),
                        },
                        "format": {
                            "type": ["string", "null"],
                            "description": (
                                "Restrict to jobs whose source OR target format is "
                                "this extension, without a dot (e.g. 'pdf')."
                            ),
                        },
                        "status": {
                            "type": ["string", "null"],
                            "description": (
                                "Restrict to one job status, e.g. 'COMPLETED' or "
                                "'FAILED'."
                            ),
                        },
                    }
                ),
            ),
        ]

    @staticmethod
    def label_for(name: str) -> str:
        """Human phrasing shown in the UI while ``name`` runs."""
        return _TOOL_LABELS.get(name, "Working on it")

    @staticmethod
    def describe(name: str, result: dict[str, Any]) -> str:
        """One-line, user-facing summary of a finished tool call.

        Derived from the tool's own result rather than produced by the model, so
        the progress line under the answer cannot contradict what actually
        happened (a model-written summary of its own tool call is exactly the
        thing that drifts).
        """
        error = result.get("error")
        if isinstance(error, str):
            return error
        if name == "list_files":
            return f"Found {result.get('count', 0)} matching file(s)"
        if name == "list_folders":
            return f"Found {result.get('count', 0)} folder(s)"
        if name == "get_file_info":
            return f"Read the details of {result.get('file_name', 'the file')}"
        if name == "list_supported_targets":
            targets = result.get("targets")
            count = len(targets) if isinstance(targets, list) else 0
            return f"Found {count} supported target format(s)"
        if name == "read_file_text":
            return f"Read {result.get('char_count', 0)} character(s)"
        if name == "summarize_file":
            return "Summarised the document"
        if name == "start_conversion":
            return (
                f"Started converting {result.get('file_name', 'the file')} to "
                f"{result.get('target_format', 'the target format')}"
            )
        if name == "get_conversion_status":
            return f"Status: {result.get('status', 'unknown')}"
        if name == "list_recent_conversions":
            count = result.get("count", 0)
            if result.get("query") or result.get("format"):
                return f"Found {count} conversion(s) matching your search"
            return f"Found {count} conversion(s)"
        if name == "create_folder":
            return f"Created the folder {result.get('name', '')}".strip()
        if name == "move_file":
            return f"Moved {result.get('file_name', 'the file')}"
        if name == "delete_file":
            # Truthful by construction: the tool did not delete anything, so the
            # line says what is actually true — the user has to confirm.
            return (
                f"Waiting for you to confirm deleting "
                f"{result.get('file_name', 'the file')}"
            )
        if name == "get_account_overview":
            jobs = result.get("jobs")
            total = jobs.get("total", 0) if isinstance(jobs, dict) else 0
            window = result.get("range")
            where = "in total" if window in (None, "all") else f"in the last {window}"
            if total:
                return f"Found {total} conversion(s) {where}"
            credits = result.get("credits_remaining")
            return f"No conversions {where}; {credits} credit(s) left"
        return "Done"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: int,
        tier: SubscriptionTier,
        artifacts: list[Artifact],
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Run tool ``name`` and return a JSON-safe result.

        ``artifacts`` is the caller's accumulator: the tool appends the files and
        jobs it touched, and that same list is how ``start_conversion`` counts
        the mutations already made in this turn.

        ``conversation_id`` is supplied only so a ``delete`` proposal can record
        which chat it belongs to: the SPA renders artifacts in floating/embedded
        components that do not receive the conversation id, so the artifact has
        to carry it. Defaults to ``None`` because a direct ``execute`` call (as
        in the unit tests, or any future non-chat caller) has no conversation;
        the artifact is still emitted, with ``"conversation_id": None``.
        """
        try:
            return await self._dispatch(
                name,
                arguments,
                user_id=user_id,
                tier=tier,
                artifacts=artifacts,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001 — a tool must never abort the loop
            # Deliberately broad: every failure mode of a tool (a storage error,
            # a database error, a parse error deep in a converter) has to reach
            # the model as a sentence it can act on, because the alternative is
            # a 500 mid-stream on a turn the user is watching.
            logger.warning("Assistant tool %s failed: %s", name, exc)
            return {"error": f"That action could not be completed: {exc}"}

    async def _dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        user_id: int,
        tier: SubscriptionTier,
        artifacts: list[Artifact],
        conversation_id: str | None,
    ) -> dict[str, Any]:
        if name == "list_files":
            return await self._list_files(user_id, arguments, artifacts)
        if name == "list_folders":
            return await self._list_folders(user_id, arguments)
        if name == "get_file_info":
            return await self._get_file_info(user_id, arguments, artifacts)
        if name == "list_supported_targets":
            return self._list_supported_targets(arguments)
        if name == "read_file_text":
            return await self._read_file_text(user_id, arguments, artifacts, tier=tier)
        if name == "summarize_file":
            return await self._summarize_file(user_id, arguments, artifacts, tier=tier)
        if name == "start_conversion":
            return await self._start_conversion(user_id, tier, arguments, artifacts)
        if name == "get_conversion_status":
            return await self._get_conversion_status(user_id, arguments, artifacts)
        if name == "list_recent_conversions":
            return await self._list_recent_conversions(user_id, arguments, artifacts)
        if name == "create_folder":
            return await self._create_folder(user_id, arguments, artifacts)
        if name == "move_file":
            return await self._move_file(user_id, arguments, artifacts)
        if name == "delete_file":
            return await self._delete_file(
                user_id, tier, arguments, artifacts, conversation_id=conversation_id
            )
        if name == "get_account_overview":
            return await self._get_account_overview(user_id, arguments)
        return {"error": f"Unknown tool {name!r}."}

    # ------------------------------------------------------------------
    # Argument helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _as_str(arguments: dict[str, Any], key: str) -> str | None:
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _as_limit(arguments: dict[str, Any], key: str, default: int) -> int:
        """Clamp a model-supplied limit into ``[1, _MAX_LIST_LIMIT]``.

        A model writing ``limit: 0`` or ``limit: 100000`` must not be able to
        produce an empty answer or an unbounded query, so the value is treated
        as a hint and clamped rather than trusted.
        """
        raw = arguments.get(key)
        if not isinstance(raw, int) or isinstance(raw, bool):
            return default
        return max(1, min(raw, _MAX_LIST_LIMIT))

    async def _resolve_folder_id(self, user_id: int, reference: str) -> str | None:
        """Resolve a folder name OR id to an id owned by ``user_id``.

        Returns ``None`` when the reference is not a folder the user owns, and
        raises nothing: callers turn that into a tool error with the list of
        known names, so the model can correct itself rather than guess again.
        """
        direct = await self._folders.get_by_id(reference)
        if direct is not None and direct.user_id == user_id:
            return direct.id

        matches = [
            folder
            for folder in await self._all_folders(user_id)
            if folder.name.casefold() == reference.casefold()
        ]
        if len(matches) == 1:
            return matches[0].id
        return None

    async def _all_folders(self, user_id: int) -> list[UserFolderModel]:
        """Every folder owned by the user.

        Walks the tree level by level (each step is one user-scoped query). A
        ``seen`` guard makes a pre-existing cycle in the data terminate instead
        of looping forever.
        """
        collected: list[UserFolderModel] = []
        seen: set[str | None] = {None}
        frontier: list[str | None] = [None]
        while frontier:
            parent = frontier.pop(0)
            rows, _total = await self._folders.list_by_parent(
                user_id, parent, offset=0, limit=_QUERY_SCAN_LIMIT
            )
            for row in rows:
                if row.id in seen:
                    continue
                seen.add(row.id)
                collected.append(row)
                frontier.append(row.id)
        return collected

    async def _folder_argument_error(
        self, user_id: int, reference: str
    ) -> dict[str, Any]:
        """Tool error listing the folder names that DO exist."""
        names = sorted({folder.name for folder in await self._all_folders(user_id)})
        if not names:
            return {
                "error": (
                    f"There is no folder called {reference!r} — this account has "
                    "no folders yet. Ask the user whether to create it."
                )
            }
        return {
            "error": (
                f"I could not tell which folder {reference!r} means. Existing "
                f"folders: {_format_list(names)}. Ask the user to pick one, or "
                "use the folder's id."
            )
        }

    async def _resolve_optional_folder(
        self, user_id: int, arguments: dict[str, Any], key: str
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Resolve an optional folder argument.

        Returns ``(folder_id, error)``; exactly one is non-``None``. A bare
        ``None``/absent argument means "the root", which is a valid answer and
        not an error.
        """
        reference = self._as_str(arguments, key)
        if reference is None:
            return None, None
        resolved = await self._resolve_folder_id(user_id, reference)
        if resolved is None:
            return None, await self._folder_argument_error(user_id, reference)
        return resolved, None

    # ------------------------------------------------------------------
    # Files and folders
    # ------------------------------------------------------------------

    async def _list_files(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        limit = self._as_limit(arguments, "limit", 20)
        query = self._as_str(arguments, "query")
        folder_ref = self._as_str(arguments, "folder")
        sort = parse_file_sort(arguments.get("sort"))
        order = parse_file_sort_order(arguments.get("order"))
        all_folders = arguments.get("all_folders") is True

        # The four shapes of this call are deliberately distinct, because they
        # search genuinely different places:
        #   folder given              -> that one folder (a `query`, if any, narrows it);
        #   no folder, `query`        -> the WHOLE drive, by name;
        #   no folder, `all_folders`  -> the WHOLE drive, no name filter;
        #   none of the above         -> the root listing.
        # Collapsing the middle case into the root listing was the bug: "find my
        # invoice" would only ever look at root-level files and miss every
        # document the user had filed away. Collapsing `all_folders` into the
        # root listing was the same bug for "what is my largest file?" — and that
        # one is worse, because "the largest of the three files at the top level"
        # is a confident wrong answer rather than an obviously empty one.
        #
        # `sort`/`order` are pushed down to the repository rather than applied
        # here: ordering a page we already fetched would rank only the rows that
        # happened to come back, which is precisely what makes a "largest file"
        # answer untrustworthy.
        if folder_ref is not None:
            folder_id = await self._resolve_folder_id(user_id, folder_ref)
            if folder_id is None:
                return await self._folder_argument_error(user_id, folder_ref)
            scan = limit if query is None else _QUERY_SCAN_LIMIT
            rows, total = await self._files.list_files(
                user_id, folder_id, offset=0, limit=scan, sort=sort, order=order
            )
            if query is not None:
                needle = query.casefold()
                rows = [row for row in rows if needle in row.file_name.casefold()]
            matched = len(rows)
            rows = rows[:limit]
            scope = "folder"
        elif query is not None:
            rows, total = await self._files.search_files(
                user_id, query, offset=0, limit=limit, sort=sort, order=order
            )
            # The repository's total is the number of matches, not the page it
            # returned, so "12 matches, showing 5" stays honest.
            matched = total
            scope = "all_folders"
        elif all_folders:
            rows, total = await self._files.list_all_files(
                user_id, offset=0, limit=limit, sort=sort, order=order
            )
            matched = len(rows)
            scope = "all_folders"
        else:
            rows, total = await self._files.list_files(
                user_id, None, offset=0, limit=limit, sort=sort, order=order
            )
            matched = len(rows)
            scope = "root"

        artifacts.extend(_file_artifact(row) for row in rows[:_MAX_ARTIFACTS_PER_TOOL])
        result: dict[str, Any] = {
            "files": [_file_payload(row) for row in rows],
            "count": matched,
            "total": total,
            "scope": scope,
            # Stated explicitly so the model describes the order it actually got
            # instead of assuming "newest first" and getting the ranking backwards
            # in its answer ("here is your largest file" over the smallest one).
            "ordered_by": {"key": sort.value, "direction": order.value},
        }
        if query is not None and matched == 0:
            result["note"] = (
                f"No file name contains {query!r}. Try listing without a query."
            )
        return result

    async def _list_folders(self, user_id: int, arguments: dict[str, Any]) -> dict[str, Any]:
        parent_ref = self._as_str(arguments, "parent")
        parent_id: str | None = None
        if parent_ref is not None:
            resolved = await self._resolve_folder_id(user_id, parent_ref)
            if resolved is None:
                return await self._folder_argument_error(user_id, parent_ref)
            parent_id = resolved

        rows, total = await self._folders.list_by_parent(
            user_id, parent_id, offset=0, limit=_MAX_LIST_LIMIT
        )
        return {
            "folders": [
                {"folder_id": row.id, "name": row.name, "parent_id": row.parent_id}
                for row in rows
            ],
            "count": len(rows),
            "total": total,
        }

    async def _get_file_info(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        file_id = self._as_str(arguments, "file_id")
        if file_id is None:
            return {"error": "A file id is required."}
        row = await self._files.get_file(user_id, file_id)
        artifacts.append(_file_artifact(row))
        return _file_payload(row)

    async def _create_folder(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        name = self._as_str(arguments, "name")
        if name is None:
            return {"error": "A folder name is required."}
        parent_id, error = await self._resolve_optional_folder(user_id, arguments, "parent")
        if error is not None:
            return error
        folder = await self._files.create_folder(user_id, name, parent_id)
        # Emitted so the answer can offer a link to the folder that was just
        # made; the raw ids below are what the model reads, the artifact is what
        # the user clicks.
        artifacts.append(_folder_artifact(folder))
        return {"folder_id": folder.id, "name": folder.name, "parent_id": folder.parent_id}

    async def _move_file(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        file_id = self._as_str(arguments, "file_id")
        if file_id is None:
            return {"error": "A file id is required."}
        folder_id, error = await self._resolve_optional_folder(user_id, arguments, "folder")
        if error is not None:
            return error
        row = await self._files.move_file(user_id, file_id, folder_id)
        artifacts.append(_file_artifact(row))
        return {"file_id": row.id, "file_name": row.file_name, "folder_id": row.folder_id}

    async def _delete_file(
        self,
        user_id: int,
        tier: SubscriptionTier,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        *,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        """Record a deletion PROPOSAL. This method never deletes anything.

        The whole security model of this feature rests on that sentence: the
        model can only *ask* for a file to be removed, and the removal itself
        happens later, from an independently authenticated confirmation click
        (``AssistantService.resolve_deletion`` -> ``delete_owned_file``). That
        is why ``self._files.delete_file`` is deliberately not called anywhere
        in this class's tool dispatch — a model that could delete directly is a
        model that a single prompt injection can turn into data loss.

        ``reason`` is accepted because the schema offers it (so the model can
        state a justification in its own prose), but it is intentionally not
        persisted: the ``delete`` artifact's ``meta`` shape is a frozen
        cross-stack contract with the SPA, which has no field for it.
        """
        action_budget = max_actions_per_turn(tier)
        if _actions_taken(artifacts) >= action_budget:
            # Same shared budget as ``start_conversion``: one instruction must
            # not let the model queue an unbounded pile of destructive actions,
            # conversions and deletion proposals together. The number in the
            # message is the one the check enforced.
            return {
                "error": (
                    f"I can propose at most {action_budget} deletions in one "
                    "message. Ask the user which one to do next."
                )
            }
        file_id = self._as_str(arguments, "file_id")
        if file_id is None:
            return {"error": "A file id is required."}
        # Delegated like every other tool: ``get_file`` refuses a foreign or
        # unknown id, so the proposal cannot be raised against a file the caller
        # does not own.
        row = await self._files.get_file(user_id, file_id)
        artifacts.append(
            Artifact(
                type="delete",
                id=row.id,
                name=row.file_name,
                meta={
                    # "pending" is the UI's single source of truth: it is what
                    # turns the chip into an actionable Confirm/Cancel prompt.
                    "state": "pending",
                    # Carried on the artifact because the components that render
                    # it (the floating mini chat) do not receive the id.
                    "conversation_id": conversation_id,
                    "extension": row.file_extension,
                    "size_bytes": row.file_size_bytes,
                    "folder_id": row.folder_id,
                },
            )
        )
        return {
            "status": "awaiting_confirmation",
            "file_id": row.id,
            "file_name": row.file_name,
            "extension": row.file_extension,
            "size_bytes": row.file_size_bytes,
            "folder_id": row.folder_id,
        }

    async def delete_owned_file(self, user_id: int, file_id: str) -> str:
        """Delete an owned file and return its display name.

        NOT reachable from the model: no tool dispatch calls this, and the tool
        signature cannot reach it. It exists solely for
        ``AssistantService.resolve_deletion``, which runs only from the
        authenticated confirmation endpoint, so the model's only path to a
        deletion remains a proposal a human had to approve.

        Ownership is enforced by ``FileService.delete_file`` itself (it calls
        ``get_file`` before touching storage), so it is deliberately not
        re-implemented here — one ownership rule, in one place.
        """
        row = await self._files.get_file(user_id, file_id)
        name = row.file_name
        await self._files.delete_file(user_id, file_id)
        return name

    # ------------------------------------------------------------------
    # Formats and conversions
    # ------------------------------------------------------------------

    def _list_supported_targets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source = self._as_str(arguments, "source_format")
        if source is None:
            return {"error": "A source format is required."}
        source = source.lstrip(".").lower()
        conversion_map = build_conversion_map()
        targets = conversion_map.get(source)
        if not targets:
            known = ", ".join(sorted(conversion_map))
            return {
                "error": (
                    f"'{source}' is not a format this service can convert. "
                    f"Supported source formats: {known}."
                )
            }
        return {"source_format": source, "targets": targets}

    async def _start_conversion(
        self,
        user_id: int,
        tier: SubscriptionTier,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
    ) -> dict[str, Any]:
        already = _actions_taken(artifacts)
        action_budget = max_actions_per_turn(tier)
        if already >= action_budget:
            # Counted from this turn's own artifacts so the limit needs no
            # per-turn state: the accumulator IS the record of what this turn
            # has already done. Started conversions AND proposed deletions share
            # the budget — both are destructive actions on the user's real data
            # — while a job a read-only tool merely *reported* is not an action
            # taken. The budget is the caller's tier entitlement (a FREE plan
            # gets 1), read from the shared policy so the message names the same
            # number the check enforced.
            return {
                "error": (
                    f"I can start at most {action_budget} conversions in one "
                    "message. Ask the user which one to do next."
                )
            }

        file_id = self._as_str(arguments, "file_id")
        target = self._as_str(arguments, "target_format")
        if file_id is None or target is None:
            return {"error": "Both file_id and target_format are required."}
        target = target.lstrip(".").lower()

        row = await self._files.get_file(user_id, file_id)
        source = row.file_extension or extension_from_filename(row.file_name)
        if not source:
            return {
                "error": (
                    f"I cannot tell what format {row.file_name!r} is in, so I do not "
                    "know what it can be converted to."
                )
            }
        supported = build_conversion_map().get(source, [])
        if target not in supported:
            return {
                "error": (
                    f"{row.file_name} is a {source} file, and {source} cannot be "
                    f"converted to '{target}'. Supported targets: "
                    f"{_format_list(supported)}."
                )
            }

        # Deliberately NO destination-folder argument. A library conversion
        # writes its output to a new object key and the worker owns that key;
        # nothing in the API files a finished output into a drive folder (the
        # SPA's "Save to Drive" does that in the browser). Accepting a folder
        # here would let the model tell the user the result was filed somewhere
        # it never was, so the argument is not offered at all.
        job = await self._conversions.convert_library_file(
            file_name=row.file_name,
            source_format=source,
            target_format=target,
            object_key=row.file_key,
            user_id=user_id,
            tier=tier,
        )
        artifacts.append(
            Artifact(
                type="job",
                id=job.job_id,
                name=row.file_name,
                meta={
                    "source_format": source,
                    "target_format": target,
                    # Marks this as an ACTION taken this turn, which is what the
                    # per-turn budget counts (see the check above).
                    "started": True,
                },
            )
        )
        return {
            "job_id": job.job_id,
            "file_name": row.file_name,
            "source_format": source,
            "target_format": target,
            "status": str(job.status),
        }

    async def _get_conversion_status(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        job_id = self._as_str(arguments, "job_id")
        if job_id is None:
            return {"error": "A job id is required."}
        job = await self._conversions.get_conversion_job(job_id)
        if not is_job_owner(job, user_id):
            if job is not None:
                # The job exists and belongs to somebody else: a genuine
                # cross-tenant probe, not a private job that never existed. This
                # is the documented IDOR signal (docs/security), and it is
                # logged only in the case that cannot be a typo by the owner.
                log_permission_denied(
                    resource="conversion_job",
                    job_id=job.job_id,
                    actor=str(user_id),
                    via="assistant_tool",
                )
            # A foreign job and a missing job are reported identically, so the
            # model cannot be used to probe whether an id exists.
            return {"error": "There is no such conversion on this account."}
        artifacts.append(
            Artifact(type="job", id=job.job_id, name=job.input_file, meta={"status": str(job.status)})
        )
        return {
            "job_id": job.job_id,
            "status": str(job.status),
            "input_file": job.input_file,
            "output_file": job.output_file,
            "error_message": job.error_message,
        }

    async def _list_recent_conversions(
        self, user_id: int, arguments: dict[str, Any], artifacts: list[Artifact]
    ) -> dict[str, Any]:
        limit = self._as_limit(arguments, "limit", 10)
        query = self._as_str(arguments, "query")
        fmt = self._as_str(arguments, "format")
        if query is not None or fmt is not None:
            rows = await self._conversions.search_jobs(
                user_id, query=query, fmt=fmt, limit=limit
            )
            total = len(rows)
        else:
            rows, total = await self._conversions.list_history(user_id, offset=0, limit=limit)
        jobs = [
            {
                "job_id": job.job_id,
                "status": str(job.status),
                "source_format": job.conversion.source_format,
                "target_format": job.conversion.target_format,
                "input_file": job.input_file,
                "output_file": job.output_file,
            }
            for job in rows
        ]
        artifacts.extend(
            Artifact(
                type="job",
                id=job.job_id,
                name=job.input_file,
                meta={"status": str(job.status)},
            )
            for job in rows[:_MAX_ARTIFACTS_PER_TOOL]
        )
        return {
            "jobs": jobs,
            "count": len(jobs),
            "total": total,
            "query": query,
            "format": fmt,
        }

    async def _get_account_overview(
        self, user_id: int, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Read-only account/usage summary.

        Deliberately does NOT touch ``artifacts``: reading the user's own
        numbers is not an action the per-turn budget should charge, and the
        caller's accumulator is the record of mutating work done this turn.
        """
        range_value = self._as_str(arguments, "range")
        fmt = self._as_str(arguments, "format")
        status = self._as_str(arguments, "status")
        normalized_fmt = fmt.lstrip(".").lower() if fmt is not None else None
        normalized_status = status.upper() if status is not None else None

        overview = await self._accounts.overview(
            user_id,
            since=_since_for_range(range_value),
            fmt=normalized_fmt,
            status=normalized_status,
        )
        return {
            "tier": overview.tier,
            "credits_remaining": overview.credits_remaining,
            "credits_reset_at": (
                overview.credits_reset_at.isoformat()
                if overview.credits_reset_at is not None
                else None
            ),
            "storage_used_bytes": overview.storage_used_bytes,
            "storage_limit_bytes": overview.storage_limit_bytes,
            "jobs": {
                "total": overview.jobs_total,
                "completed": overview.jobs_completed,
                "failed": overview.jobs_failed,
                "active": overview.jobs_active,
            },
            "by_target_format": [
                {"target_format": target, "count": count}
                for target, count in overview.by_target_format
            ],
            "range": range_value if range_value in _RANGE_WINDOWS else "all",
            "filter": {"format": normalized_fmt, "status": normalized_status},
        }

    # ------------------------------------------------------------------
    # Document reading
    # ------------------------------------------------------------------

    async def read_document(
        self, *, user_id: int, file_id: str, tier: SubscriptionTier
    ) -> tuple[UserFileModel, ExtractedDocument] | dict[str, Any]:
        """Fetch a file (with ownership) and extract its text.

        Returns the row plus the extraction, or a tool error dict. Public
        because the non-streaming ``POST /summarize`` endpoint shares this path
        with the ``summarize_file`` tool — the two must read a document exactly
        the same way, limits included.

        ``tier`` is required rather than read from ``self`` because the toolbox
        is shared between requests: the byte budget is the MINIMUM of the
        deployment ceiling (``AI_MAX_DOCUMENT_BYTES``) and the tier's
        entitlement, which is what makes a lower plan genuinely cheaper to serve
        while still respecting an operator's hard cap. Picking the minimum (not
        the tier value alone) means raising a plan's entitlement can never
        exceed what the process is configured to buffer.
        """
        row = await self._files.get_file(user_id, file_id)
        budget = min(self._max_document_bytes, max_document_bytes_for_tier(tier))
        stats = await self._storage.stat_object(row.file_key) or {}
        size = int(stats.get("size") or 0)
        if size > budget:
            return {
                "error": (
                    f"{row.file_name} is {size // (1024 * 1024)} MB, larger than the "
                    f"{budget // (1024 * 1024)} MB I can read. "
                    "Suggest converting it to a smaller format first."
                )
            }
        # Read at most the byte budget, so a lying/absent stat_object cannot
        # make us buffer an unbounded object in memory.
        data = await self._storage.read_object_head(row.file_key, budget)
        if not data:
            return {"error": f"{row.file_name} could not be read from storage."}
        # Parsing is CPU-bound and has no I/O: off the event loop so a large
        # PDF does not stall every other request in the process.
        extracted = await asyncio.to_thread(
            self._extractor.extract, file_name=row.file_name, data=data
        )
        return row, extracted

    async def source_format_for(self, *, user_id: int, file_id: str) -> str:
        """The source extension of an owned file (``""`` when undeterminable).

        Ownership is enforced here by ``get_file``, so a caller cannot use this
        to learn the extension of a file it does not own.
        """
        row = await self._files.get_file(user_id, file_id)
        return row.file_extension or extension_from_filename(row.file_name)

    async def _document_text(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        *,
        tier: SubscriptionTier,
    ) -> tuple[ExtractedDocument | None, dict[str, Any] | None]:
        """Shared read path for ``read_file_text`` and ``summarize_file``."""
        file_id = self._as_str(arguments, "file_id")
        if file_id is None:
            return None, {"error": "A file id is required."}
        extracted = await self.read_document(user_id=user_id, file_id=file_id, tier=tier)
        if isinstance(extracted, dict):
            return None, extracted
        row, document = extracted
        artifacts.append(_file_artifact(row))
        if not document.supported:
            return None, {
                "error": (
                    f"I cannot read the text of {row.file_name}: {document.note}. "
                    "Tell the user they could convert it to txt or md first, "
                    "or ask what they want to do with it."
                )
            }
        if not document.text.strip():
            return None, {
                "error": f"{row.file_name} appears to contain no readable text."
            }
        return document, None

    async def _read_file_text(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        *,
        tier: SubscriptionTier,
    ) -> dict[str, Any]:
        document, error = await self._document_text(
            user_id, arguments, artifacts, tier=tier
        )
        if error is not None:
            return error
        assert document is not None
        requested = arguments.get("max_chars")
        if isinstance(requested, int) and not isinstance(requested, bool) and requested > 0:
            budget = min(requested, self._summary_max_input_chars)
        else:
            budget = min(_DEFAULT_READ_CHARS, self._summary_max_input_chars)
        text = document.text[:budget]
        truncated = document.truncated or len(document.text) > budget
        result: dict[str, Any] = {
            "text": text,
            "char_count": len(text),
            "truncated": truncated,
        }
        if truncated:
            result["note"] = "The document is longer than I read; the text is cut off."
        return result

    async def _summarize_file(
        self,
        user_id: int,
        arguments: dict[str, Any],
        artifacts: list[Artifact],
        *,
        tier: SubscriptionTier,
    ) -> dict[str, Any]:
        document, error = await self._document_text(
            user_id, arguments, artifacts, tier=tier
        )
        if error is not None:
            return error
        assert document is not None
        return await self.summarize_text(document.text, tier=tier)

    async def summarize_text(
        self, text: str, *, tier: SubscriptionTier
    ) -> dict[str, Any]:
        """Summarise ``text`` with the tier's model, falling back to extraction.

        Why the fallback is not optional: the ``echo`` backend has no model at
        all, and any provider can return prose where JSON was asked for. In both
        cases the user still gets a usable summary (the first sentences plus the
        leading lines) instead of an error, which is the whole point of a
        feature that is meant to work with no API key configured.

        ``tier`` selects the model: a bigger plan gets the better model for the
        same document, which is the visible half of the entitlement (the byte
        budget in ``read_document`` is the other).
        """
        llm = self._models.for_tier(tier)
        if llm.model == "echo":
            # Skip a pointless round-trip to a backend that cannot summarise.
            return self._extractive_summary(text)

        messages = [
            LlmMessage(role="system", content=SUMMARY_PROMPT),
            LlmMessage(role="user", content=text[: self._summary_max_input_chars]),
        ]
        raw = ""
        async for chunk in llm.stream(messages=messages, tools=[]):
            if chunk.kind == "text":
                raw += chunk.text
            elif chunk.response is not None:
                raw = chunk.response.content or raw
        parsed = _parse_json_object(raw)
        summary = parsed.get("summary") if parsed else None
        if not isinstance(summary, str) or not summary.strip():
            return self._extractive_summary(text)
        points_raw = parsed.get("key_points") if parsed else None
        points = (
            [point for point in points_raw if isinstance(point, str)]
            if isinstance(points_raw, list)
            else []
        )
        return {"summary": summary.strip(), "key_points": points[:6]}

    @staticmethod
    def _extractive_summary(text: str) -> dict[str, Any]:
        """Deterministic, model-free summary.

        The first few sentences plus the longest lines: crude, but honest — it
        quotes the document rather than pretending to have understood it, and it
        is exactly reproducible, which is what makes the ``echo`` backend usable
        in tests.
        """
        collapsed = " ".join(text.split())
        sentences = [part.strip() for part in collapsed.replace("! ", ". ").replace("? ", ". ").split(". ") if part.strip()]
        summary = ". ".join(sentences[:3]).strip()
        if summary and not summary.endswith("."):
            summary += "."
        if not summary:
            summary = collapsed[:400]
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 3]
        key_points = sorted(lines, key=len, reverse=True)[:5]
        return {
            "summary": summary[:1000],
            "key_points": key_points,
            "extractive": True,
        }


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a model reply as a JSON object, tolerating a Markdown fence.

    Returns ``None`` when the reply is not a JSON object, which is the signal to
    fall back — never an error, because "the model did not cooperate" is a
    normal outcome that must degrade into a still-useful answer.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed: Any = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
