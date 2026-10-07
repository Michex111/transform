"""The MCP tools: what an AI agent may actually do to a user's documents.

This is the MCP analogue of ``AssistantToolBox``, and it holds to the same two
invariants — for the same reasons:

* **Ownership is delegated, never re-implemented.** Files and jobs are fetched
  through ``FileService`` / ``ConversionService``, which already refuse rows the
  caller does not own. A tool must never be the weak link that lets an agent
  reach another user's document.
* **Failures are values, not exceptions.** A tool returns
  ``{"ok": False, "error": "..."}`` so the agent can tell the user what went
  wrong and try something else. Raising would turn a mistyped file id into a
  dead conversation.

What is *different* from the assistant's toolbox, and why:

* **Every tool declares a required scope**, checked before anything else runs.
  An agent that was granted read-only access cannot convert, and one granted
  read+convert cannot delete — not because the model is trusted to behave but
  because the capability is absent from its token.
* **No storage key is ever accepted or returned.** Tools take ``file_id`` /
  ``job_id`` and resolve them server-side. ``ConversionService`` will happily
  accept a raw ``object_key``, so an agent could otherwise be handed the ability
  to name an object it was never authorized to read.
* **``save_file`` is not "upload".** An agent cannot introduce arbitrary content
  into a user's Drive; it can only promote a document Transform itself produced
  from a file the user already owns.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from src.application.exceptions.file_system_exceptions import (
    FileRecordNotFoundError,
    FileSystemError,
    FolderNotFoundError,
)
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.domain.conversions.value_object.job_origin import JobOrigin
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.value_object.agent_scope import AgentScope, covers
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.storage.sanitize import (
    extension_from_filename,
    normalize_extension,
)
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.infrastructure.database.models import UserFileModel
from src.infrastructure.logging.audit import log_data_access, log_permission_denied

logger = logging.getLogger(__name__)

#: Hard ceiling on rows any listing tool will return, whatever the model asked
#: for. The ``limit`` argument is untrusted (it is a number the model wrote), and
#: an unbounded list is a token bomb that would overflow the context window and
#: the operator's bill.
_MAX_LIST_LIMIT = 50
#: Formats one listing may be filtered by, mirroring the assistant's cap.
_MAX_EXTENSION_FILTERS = 8
#: A single tool call must not be able to name an unbounded scope set.
_MAX_TARGET_FORMATS_REPORTED = 40


@dataclass(frozen=True)
class MCPToolContext:
    """The verified identity and authority behind one MCP tool call.

    Built by the transport from the *token* — never from a tool argument. The
    ``user_id`` here is what makes ownership checks possible; if a tool ever
    accepted a user id from its arguments, this whole layer would be pointless.
    """

    user_id: int
    scopes: tuple[AgentScope, ...]
    tier: SubscriptionTier = SubscriptionTier.FREE
    client_id: str = ""
    grant_id: str = ""

    def allows(self, scope: AgentScope) -> bool:
        return covers(self.scopes, scope)


@runtime_checkable
class MCPFileServicePort(Protocol):
    """The (ownership-checked) file operations the MCP tools use.

    Declared narrowly rather than depending on ``FileService`` so the exact
    surface an agent may touch is readable in one place.
    """

    async def get_file(self, user_id: int, file_id: str) -> UserFileModel: ...

    async def list_files(
        self, user_id: int, folder_id: str | None = None, *, offset: int = 0, limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]: ...

    async def list_all_files(
        self, user_id: int, *, offset: int = 0, limit: int = 20,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]: ...

    async def search_files(
        self, user_id: int, query: str, *, offset: int = 0, limit: int = 50,
        extensions: Sequence[str] | None = None,
    ) -> tuple[list[UserFileModel], int]: ...

    async def delete_file(self, user_id: int, file_id: str) -> None: ...

    async def complete_upload(self, user_id: int, session: Any) -> str: ...


@runtime_checkable
class MCPConversionServicePort(Protocol):
    """The (partly ownership-checked) conversion operations the MCP tools use."""

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
    ) -> ConversionJob: ...

    async def get_conversion_job(self, job_id: str) -> ConversionJob | None: ...


@runtime_checkable
class MCPTransferServicePort(Protocol):
    """Just enough of the upload session flow to promote a produced file."""

    async def create_upload(
        self,
        file_extension: str,
        user_id: str,
        file_name: str | None = None,
        folder_id: str | None = None,
        file_size: int | None = None,
        max_file_size_bytes: int | None = None,
    ) -> Any: ...

    async def verify_upload_completion(self, upload_id: str, parts: Any = None) -> Any: ...

    async def delete_upload_session(self, upload_id: str) -> None:
        """Discard a reserved upload session whose copy failed.

        Needed so a failed ``save_file`` does not leave a half-written object
        and a dangling cache entry behind.
        """
        ...


@runtime_checkable
class MCPObjectCopierPort(Protocol):
    """Copy one object to another key inside the same bucket.

    Deliberately the *only* storage capability the toolbox holds: no delete, no
    overwrite, no presigning, and no way to name a destination outside the
    upload namespace it is given.
    """

    async def copy_object(self, source_key: str, target_key: str) -> int: ...


def _file_payload(row: UserFileModel) -> dict[str, Any]:
    """JSON-safe description of a file. Never includes ``file_key``."""
    return {
        "file_id": row.id,
        "file_name": row.file_name,
        "extension": row.file_extension,
        "size_bytes": row.file_size_bytes,
        "folder_id": row.folder_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _job_payload(job: ConversionJob) -> dict[str, Any]:
    """JSON-safe description of a conversion job.

    ``object_key`` and ``data_key_wrapped`` are omitted on purpose: an agent has
    no legitimate use for them, and handing one over would disclose where a
    user's file physically lives.
    """
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "status": str(job.status),
        "source_format": job.conversion.source_format,
        "target_format": job.conversion.target_format,
        "input_file": job.input_file,
        "output_file": job.output_file,
        "progress": job.progress,
        "created_at": _iso(job.created_at),
        "error_message": job.error_message,
        "download_available": job.status is JobStatus.COMPLETED,
    }
    return payload


def _normalize_extension_filter(values: Any) -> tuple[str, ...]:
    """Reduce the model's ``extension`` argument to normalised, bounded values.

    Accepts one string or a list. The app's own vocabulary is a *category* word
    ("spreadsheets"), which the schema description expands for the model, so a
    value that normalises to nothing is treated as "no filter" — it widens
    rather than silently emptying the result.
    """
    if values is None:
        return ()
    raw = [values] if isinstance(values, str) else list(values)
    normalized = {normalize_extension(str(value)) for value in raw}
    return tuple(sorted(value for value in normalized if value))[:_MAX_EXTENSION_FILTERS]


class MCPToolBox:
    """Executes MCP tools against the file, conversion and transfer services."""

    def __init__(
        self,
        *,
        file_service: MCPFileServicePort,
        conversion_service: MCPConversionServicePort,
        transfer_service: MCPTransferServicePort,
        copier: MCPObjectCopierPort,
        max_list_limit: int = _MAX_LIST_LIMIT,
        conversion_map: dict[str, list[str]] | None = None,
    ) -> None:
        self._files = file_service
        self._conversions = conversion_service
        self._transfers = transfer_service
        self._copier = copier
        self._max_list_limit = max_list_limit
        #: Injected so tests can pin the graph; production reads the real
        #: registry (the same source the REST ``/supported`` endpoint uses).
        self._map = conversion_map or build_conversion_map()

    # ------------------------------------------------------------------
    # Read-only tools
    # ------------------------------------------------------------------

    async def get_supported_conversions(
        self, ctx: MCPToolContext, source_format: str | None = None
    ) -> dict[str, Any]:
        """List the conversions this deployment can perform."""
        denied = self._require(ctx, AgentScope.DOCUMENTS_READ, "get_supported_conversions")
        if denied:
            return denied
        source = normalize_extension(source_format) if source_format else None
        if source:
            targets = self._map.get(source)
            if not targets:
                return {
                    "ok": False,
                    "error": f"{source!r} is not a supported input format.",
                    "hint": "Call get_supported_conversions without a source_format to list them all.",
                }
            return {"ok": True, "source_format": source, "target_formats": targets}
        return {
            "ok": True,
            "conversions": self._map,
            "source_format_count": len(self._map),
            "target_format_count": len({t for targets in self._map.values() for t in targets}),
        }

    async def list_files(
        self,
        ctx: MCPToolContext,
        query: str | None = None,
        extension: Any = None,
        folder_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List or search the caller's own files."""
        denied = self._require(ctx, AgentScope.DOCUMENTS_READ, "list_files")
        if denied:
            return denied
        limit = self._clamp_limit(limit)
        offset = max(0, int(offset or 0))
        extensions = _normalize_extension_filter(extension)

        if query:
            rows, total = await self._files.search_files(
                ctx.user_id, query, offset=offset, limit=limit, extensions=extensions or None
            )
        elif folder_id is not None:
            try:
                rows, total = await self._files.list_files(
                    ctx.user_id, folder_id, offset=offset, limit=limit, extensions=extensions or None
                )
            except FolderNotFoundError:
                return {"ok": False, "error": "That folder was not found."}
        else:
            # Whole drive by default: a file inside a folder is still one of
            # the user's files, and an agent cannot be expected to know where
            # a document lives before it has listed anything.
            rows, total = await self._files.list_all_files(
                ctx.user_id, offset=offset, limit=limit, extensions=extensions or None
            )

        self._audit(ctx, "list_files", f"files:{len(rows)}")
        note = None
        if not rows and query:
            note = "No file names matched that search."
        elif not rows and extensions:
            note = (
                "No files had any of those extensions. If you filtered by a "
                "category word, try the concrete extensions for it."
            )
        payload: dict[str, Any] = {
            "ok": True,
            "files": [_file_payload(row) for row in rows],
            "total": total,
            "returned": len(rows),
        }
        if extensions:
            payload["filtered_by_extension"] = list(extensions)
        if note:
            payload["note"] = note
        return payload

    async def get_file(self, ctx: MCPToolContext, file_id: str) -> dict[str, Any]:
        """Describe one of the caller's files."""
        denied = self._require(ctx, AgentScope.DOCUMENTS_READ, "get_file")
        if denied:
            return denied
        row = await self._owned_file(ctx, file_id)
        if isinstance(row, dict):
            return row
        self._audit(ctx, "get_file", f"file:{row.id}")
        return {"ok": True, "file": _file_payload(row)}

    async def get_conversion_status(
        self, ctx: MCPToolContext, job_id: str
    ) -> dict[str, Any]:
        """Report the status of a conversion the caller started."""
        denied = self._require(ctx, AgentScope.DOCUMENTS_READ, "get_conversion_status")
        if denied:
            return denied
        job = await self._conversions.get_conversion_job(job_id)
        if not is_job_owner(job, ctx.user_id):
            # A missing job and someone else's job are reported identically, so
            # a guessed id cannot be probed for existence.
            log_permission_denied(
                resource=f"job:{job_id}",
                user_id=str(ctx.user_id),
                action="mcp.tool_call",
                tool="get_conversion_status",
            )
            return {"ok": False, "error": "No such conversion was found."}
        self._audit(ctx, "get_conversion_status", f"job:{job_id}")
        return {"ok": True, "job": _job_payload(job)}

    # ------------------------------------------------------------------
    # Mutating tools
    # ------------------------------------------------------------------

    async def convert_file(
        self, ctx: MCPToolContext, file_id: str, target_format: str
    ) -> dict[str, Any]:
        """Convert one of the caller's files, creating a new output.

        The original is never modified; the conversion is asynchronous and the
        tool returns as soon as the job is accepted, so the caller polls
        ``get_conversion_status``. The output only becomes a Drive file if the
        caller asks for it with ``save_file``.
        """
        denied = self._require(ctx, AgentScope.DOCUMENTS_CONVERT, "convert_file")
        if denied:
            return denied
        row = await self._owned_file(ctx, file_id)
        if isinstance(row, dict):
            return row

        source = normalize_extension(row.file_extension) or extension_from_filename(row.file_name)
        target = normalize_extension(target_format)
        if not target:
            return {"ok": False, "error": "target_format must be a file extension such as 'pdf'."}
        supported = self._map.get(source, [])
        if target not in supported:
            return {
                "ok": False,
                "error": f"Cannot convert {source or 'that file'} to {target}.",
                "source_format": source,
                "supported_target_formats": supported[:_MAX_TARGET_FORMATS_REPORTED],
            }

        job = await self._conversions.convert_library_file(
            file_name=row.file_name,
            source_format=source,
            target_format=target,
            # Resolved server-side from the owned row — an agent can never
            # supply this value, which is what stops it reading arbitrary
            # objects out of the bucket.
            object_key=row.file_key,
            user_id=ctx.user_id,
            tier=ctx.tier,
            origin=JobOrigin.API,
        )
        self._audit(ctx, "convert_file", f"file:{row.id}->job:{job.job_id}")
        return {
            "ok": True,
            "job": _job_payload(job),
            "note": (
                "The conversion is running in the background. Poll "
                "get_conversion_status with this job_id. The original file is unchanged."
            ),
        }

    async def save_file(
        self,
        ctx: MCPToolContext,
        job_id: str,
        folder_id: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        """Save a completed conversion's output into the caller's Drive.

        This is the only way an agent can create a file, and it is deliberately
        not an upload: the bytes must already exist as the output of a job the
        caller owns. Arbitrary content cannot be introduced.

        All the real rules (extension/type validation, per-file cap, account
        quota, folder ownership) are enforced by
        ``FileService.complete_upload`` — this method only sequences the calls.
        """
        denied = self._require(ctx, AgentScope.DOCUMENTS_WRITE, "save_file")
        if denied:
            return denied

        job = await self._conversions.get_conversion_job(job_id)
        if not is_job_owner(job, ctx.user_id):
            log_permission_denied(
                resource=f"job:{job_id}",
                user_id=str(ctx.user_id),
                action="mcp.tool_call",
                tool="save_file",
            )
            return {"ok": False, "error": "No such conversion was found."}
        if job.status is not JobStatus.COMPLETED:
            return {
                "ok": False,
                "error": f"That conversion is {str(job.status).lower()}; only a completed conversion can be saved.",
                "job": _job_payload(job),
            }
        if job.client_encrypted or job.data_key_wrapped:
            # Copying a client-encrypted output into the library as-is would
            # store ciphertext under a plaintext name. Refusing is the honest
            # outcome; the user can still download the job output directly.
            return {
                "ok": False,
                "error": "That conversion's output is encrypted with a key only your browser holds, "
                "so it cannot be saved to your Drive from here.",
            }
        output_name = file_name or self._default_output_name(job)
        extension = extension_from_filename(output_name)
        if not extension:
            return {"ok": False, "error": "A file name with an extension is required."}
        if not job.output_file:
            # A COMPLETED job always has one, but the type allows for the
            # impossible case and copying "" would be a silent no-op.
            return {"ok": False, "error": "That conversion produced no output to save."}
        session = await self._transfers.create_upload(
            extension,
            str(ctx.user_id),
            file_name=output_name,
            folder_id=folder_id,
            # ``file_size`` is deliberately NOT passed. It is only used to
            # choose a multipart session, and the copier below writes the whole
            # object in one PUT — so declaring a size at or above the multipart
            # threshold would reserve a multipart upload that is never started,
            # and ``verify_upload_completion`` would then fail trying to
            # complete it. Omitting it always yields the single-PUT session this
            # path actually uses. Nothing is weakened by this: the size is
            # *measured* from the stored object by ``complete_upload``, which is
            # where the per-file cap and the account quota are enforced.
            file_size=None,
        )
        try:
            # Stream the produced object to the freshly reserved library key,
            # then verify + commit through the ordinary upload path so every
            # validation (magic bytes, quota, folder ownership) still applies.
            await self._copier.copy_object(job.output_file, session.object_key)
            upload = await self._transfers.verify_upload_completion(session.upload_id)
            file_id = await self._files.complete_upload(ctx.user_id, upload)
        except (FileSystemError, OSError) as exc:
            await self._transfers.delete_upload_session(session.upload_id)
            logger.warning("MCP save_file failed for job %s: %s", job_id, exc)
            return {"ok": False, "error": f"The file could not be saved: {exc}"}
        except Exception as exc:  # noqa: BLE001 - storage/transfer failures
            await self._transfers.delete_upload_session(session.upload_id)
            logger.warning("MCP save_file failed for job %s: %s", job_id, exc)
            return {"ok": False, "error": "The file could not be saved because storage was unavailable."}

        row = await self._owned_file(ctx, file_id)
        self._audit(ctx, "save_file", f"job:{job_id}->file:{file_id}")
        return {
            "ok": True,
            "file": row["file"] if isinstance(row, dict) and row.get("ok") else {"file_id": file_id},
            "note": "Saved to your Drive. The original conversion output is still available for download.",
        }

    async def delete_file(self, ctx: MCPToolContext, file_id: str) -> dict[str, Any]:
        """Permanently delete one of the caller's files.

        The most destructive capability in the product, so it is the most
        narrowly granted: it needs ``documents.delete``, which the consent
        screen never pre-selects.
        """
        denied = self._require(ctx, AgentScope.DOCUMENTS_DELETE, "delete_file")
        if denied:
            return denied
        row = await self._owned_file(ctx, file_id)
        if isinstance(row, dict):
            return row
        name = row.file_name
        await self._files.delete_file(ctx.user_id, file_id)
        self._audit(ctx, "delete_file", f"file:{file_id}")
        return {"ok": True, "deleted_file_id": file_id, "file_name": name}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require(
        self, ctx: MCPToolContext, scope: AgentScope, tool: str
    ) -> dict[str, Any] | None:
        """Return a refusal payload when ``scope`` was not granted.

        Checked *before* any data is touched, and audited as a permission
        denial, so an under-scoped agent is visible in the security log rather
        than merely told "no".
        """
        if ctx.allows(scope):
            return None
        log_permission_denied(
            resource=f"scope:{scope.value}",
            user_id=str(ctx.user_id),
            action="mcp.tool_call",
            tool=tool,
            missing_scope=scope.value,
        )
        return {
            "ok": False,
            "error": (
                f"This application was not granted the {scope.value!r} permission, "
                f"so {tool} is not available. Ask the user to reconnect and allow it."
            ),
            "required_scope": scope.value,
        }

    async def _owned_file(
        self, ctx: MCPToolContext, file_id: str
    ) -> UserFileModel | dict[str, Any]:
        """Fetch a file the caller owns, or a refusal payload.

        ``FileService.get_file`` already fails closed on a foreign id; the
        exception is translated here so the agent gets a sentence it can relay
        instead of a stack trace, and the attempt is audited.
        """
        try:
            return await self._files.get_file(ctx.user_id, file_id)
        except FileRecordNotFoundError:
            log_permission_denied(
                resource=f"file:{file_id}",
                user_id=str(ctx.user_id),
                action="mcp.tool_call",
            )
            return {"ok": False, "error": "No such file was found in your Drive."}

    def _clamp_limit(self, limit: Any) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            value = 20
        return max(1, min(value, self._max_list_limit))

    @staticmethod
    def _default_output_name(job: ConversionJob) -> str:
        """Name for a saved output, derived from the path Transform produced.

        The worker's real output name is authoritative — a multi-page
        ``pdf → png`` job emits a ``.zip``, so naming it after ``target_format``
        would store an archive with an image extension.
        """
        if job.output_file:
            return job.output_file.rsplit("/", 1)[-1]
        return f"converted.{job.conversion.target_format}"

    @staticmethod
    def _audit(ctx: MCPToolContext, tool: str, resource: str) -> None:
        """Record a successful agent action in the security audit log."""
        log_data_access(
            user_id=str(ctx.user_id),
            action="mcp.tool_call",
            resource=resource,
            tool=tool,
            client_id=ctx.client_id,
            grant_id=ctx.grant_id,
        )
