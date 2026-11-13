"""Short-lived, single-use capabilities that receive one attached file.

WHY this exists: an MCP client's chat attachment never arrives here. The model
is shown extracted text and images, not the original bytes, so it cannot rebuild
a PDF, DOCX, XLSX or image for ``upload_file(content_base64)``. This service is
the missing byte path — the agent mints a capability, the client's code sandbox
(or a browser page) uploads the real file to the endpoint, and the server stores
it through the ordinary upload pipeline (``create_upload`` → storage write →
``verify_upload_completion`` → ``FileService.complete_upload``), so the tier
per-file cap, the account quota, the extension and magic-byte checks, and folder
ownership all still apply.

Two properties are load-bearing, and they mirror the download capability:

* **The capability is bound to the grant, not to the access token.** Its payload
  is only ``{"intent_id": ...}``. A token refresh therefore cannot kill a pending
  upload, and revoking, pausing or narrowing the grant *does* kill it, because
  every redemption re-reads the grant as it is now.
* **Failures are one answer.** A used, expired, tampered or unauthorized
  capability is indistinguishable from a missing one, so the link cannot be used
  as an oracle for grants, folders or file ids.

Nothing here accepts or returns a storage key or a bucket: the endpoint resolves
the object key server-side from the upload session it created.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from src.application.exceptions.file_system_exceptions import FileSystemError
from src.application.services.mcp.toolbox import MCPToolContext, _job_payload
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.security.enitities.mcp_upload_intent import (
    MCPUploadIntent,
    MCPUploadIntentStatus,
)
from src.domain.security.value_object.agent_access_scope import (
    FolderAccess,
    folder_scope_is_usable,
)
from src.domain.security.value_object.agent_scope import AgentScope
from src.infrastructure.adapters.storage.sanitize import (
    UnsafeObjectKeyError,
    extension_from_filename,
    normalize_extension,
    sanitize_filename,
)
from src.infrastructure.converters.conversion_map import build_conversion_map
from src.infrastructure.logging.audit import log_data_access

#: Header that carries the capability. Never the query string: query strings
#: reach access logs, proxies and Referer headers, which is exactly how a bearer
#: capability leaks.
UPLOAD_HEADER_NAME = "X-Transform-Upload"
#: The endpoint the capability is redeemed against (and the URL the tool hands
#: to a sandbox for its ``curl`` command).
UPLOAD_PATH = "/api/v1/mcp/upload"
#: The one refusal for every unavailable capability. Single message on purpose:
#: distinguishing "used" from "expired" from "revoked" would turn the link into
#: an oracle.
UPLOAD_UNAVAILABLE_MESSAGE = "This upload link is unavailable or has expired."
#: Maximum accepted capability length. A Fernet token for a UUID payload is a
#: few hundred characters; anything larger is malformed and must be refused
#: before an HMAC is computed over it.
_MAX_CAPABILITY_LENGTH = 4096
#: Ceiling on the list of target formats echoed in a "cannot convert" error,
#: matching the ``convert_file`` tool.
_MAX_TARGET_FORMATS_REPORTED = 40


class UploadUnavailable(Exception):
    """The capability or its underlying authorization is unavailable."""


class _IntentRepository(Protocol):
    async def create(self, intent: MCPUploadIntent) -> MCPUploadIntent: ...
    async def get(self, intent_id: str) -> MCPUploadIntent | None: ...
    async def start_receiving(
        self, intent_id: str, *, now: datetime
    ) -> MCPUploadIntent | None: ...
    async def mark_completed(
        self, intent_id: str, *, file_id: str, job_id: str | None
    ) -> None: ...
    async def mark_failed(self, intent_id: str, *, error: str) -> None: ...


class _Grants(Protocol):
    async def get_grant(self, grant_id: str) -> Any: ...


class _Files(Protocol):
    async def authorize_upload_size(self, user_id: int, declared_size: int | None) -> int: ...
    async def resolve_folder_scope(self, user_id: int, folder_id: str) -> set[str] | None: ...


class _Conversions(Protocol):
    async def get_conversion_job(self, job_id: str) -> ConversionJob | None: ...


class _Documents(Protocol):
    """The document-tool surface this service reuses rather than re-implements.

    ``MCPToolBox`` implements it. Reusing it is what keeps the destination rule
    (inside the grant's subtree, defaulting to the bound folder) and the
    refusal messages identical between ``upload_file`` and this path.
    """

    def require(
        self, ctx: MCPToolContext, scope: AgentScope, tool: str
    ) -> dict[str, Any] | None: ...

    async def resolve_upload_destination(
        self, ctx: MCPToolContext, folder_id: str | None, tool: str
    ) -> tuple[str | None, dict[str, Any] | None]: ...

    async def get_file(self, ctx: MCPToolContext, file_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CreatedUploadLink:
    """Everything a client needs to perform the upload, with no storage detail."""

    intent: MCPUploadIntent
    capability: str
    upload_url: str
    expires_in_seconds: int

    @property
    def header_name(self) -> str:
        return UPLOAD_HEADER_NAME

    @property
    def upload_page_url(self) -> str:
        """The browser page, with the capability in the URL *fragment*.

        Fragments are not sent to the server, so this URL never places the
        capability in an access log or a proxy's request line.
        """
        return f"{self.upload_url}#{self.capability}"

    @property
    def curl_command(self) -> str:
        """The exact command an agent can run in its own sandbox.

        Deliberately a single line with a parameterised path: the model supplies
        the local path of the attachment in its sandbox, and nothing else.
        """
        return (
            "curl --fail -sS -X PUT "
            f"-H '{UPLOAD_HEADER_NAME}: {self.capability}' "
            f"--data-binary @'<path to file>' '{self.upload_url}'"
        )


class MCPUploadCapabilities:
    """Mint, redeem and report single-use upload capabilities."""

    def __init__(
        self,
        *,
        intents: _IntentRepository,
        grants: _Grants,
        files: _Files,
        conversions: _Conversions,
        documents: _Documents | None,
        secret: str,
        resource_url: str,
        max_attachment_bytes: int,
        ttl_seconds: int,
        start_conversion: Callable[[MCPUploadIntent], Awaitable[dict[str, Any]]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_attachment_bytes <= 0:
            raise ValueError("max_attachment_bytes must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._intents = intents
        self._grants = grants
        self._files = files
        self._conversions = conversions
        self._documents = documents
        self._start_conversion = start_conversion
        self._max_attachment_bytes = max_attachment_bytes
        self._ttl = ttl_seconds
        parts = urlsplit(resource_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("Invalid MCP resource URL")
        self._upload_url = f"{parts.scheme}://{parts.netloc}{UPLOAD_PATH}"
        # Domain-separated from SECRET_KEY and from the download capability, so
        # a token from one purpose can never be replayed as the other.
        key = hmac.new(secret.encode(), b"mcp-upload-capability-v1", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))
        self._clock = clock

    @property
    def upload_url(self) -> str:
        return self._upload_url

    @staticmethod
    def fingerprint(capability: str | None) -> str:
        """A 12-character SHA-256 prefix, safe to log.

        Never log the capability itself: it is a bearer credential for one
        upload, and a log is exactly the kind of place another operator can read.
        """
        return hashlib.sha256((capability or "").encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def create_intent(
        self,
        ctx: MCPToolContext,
        *,
        file_name: str,
        size_bytes: int | None = None,
        folder_id: str | None = None,
        target_format: str | None = None,
    ) -> dict[str, Any] | CreatedUploadLink:
        """Authorize one upload and mint its capability, or return a refusal.

        The refusal is the ordinary ``{"ok": false, "error": ...}`` envelope the
        other tools return, so the agent can tell the user what to fix. The
        success value is a :class:`CreatedUploadLink`; the tool layer turns it
        into a typed result.
        """
        denied = self._documents_required().require(
            ctx, AgentScope.DOCUMENTS_WRITE, "create_upload_link"
        )
        if denied:
            return denied
        if target_format is not None:
            # Starting a conversion after the upload is a conversion action, so
            # it needs the conversion permission *at creation time*. It is
            # re-checked at completion, because the grant can change in between.
            denied = self._documents_required().require(
                ctx, AgentScope.DOCUMENTS_CONVERT, "create_upload_link"
            )
            if denied:
                return denied

        destination, refused = await self._documents_required().resolve_upload_destination(
            ctx, folder_id, "create_upload_link"
        )
        if refused:
            return refused

        try:
            safe_name = sanitize_filename(file_name)
        except UnsafeObjectKeyError:
            return {
                "ok": False,
                "error": "file_name must be a plain file name with an extension, e.g. 'report.pdf'.",
            }
        extension = extension_from_filename(safe_name)
        if not extension:
            return {
                "ok": False,
                "error": "file_name must include a file extension such as 'pdf'.",
            }

        normalized_target = normalize_extension(target_format) if target_format else None
        if target_format is not None and not normalized_target:
            return {
                "ok": False,
                "error": "target_format must be a file extension such as 'docx'.",
            }
        if normalized_target:
            supported = build_conversion_map().get(extension, [])
            if normalized_target not in supported:
                return {
                    "ok": False,
                    "error": f"Cannot convert {extension} to {normalized_target}.",
                    "source_format": extension,
                    "target_formats": supported[:_MAX_TARGET_FORMATS_REPORTED],
                    "hint": (
                        "Pick one of target_formats, or call get_supported_conversions "
                        f"with source_format={extension!r} for the same list."
                    ),
                }

        # Advisory pre-flight: the tier's per-file cap and the account quota, so
        # an impossible upload is refused before a byte moves. The authoritative
        # check still runs inside ``complete_upload`` on the measured size.
        try:
            tier_cap = await self._files.authorize_upload_size(ctx.user_id, size_bytes)
        except FileSystemError as exc:
            return {"ok": False, "error": str(exc)}

        max_bytes = min(tier_cap, self._max_attachment_bytes)
        if size_bytes is not None and size_bytes > max_bytes:
            return {
                "ok": False,
                "error": (
                    f"That file is larger than the {max_bytes // (1024 * 1024)} MB "
                    "limit for this upload link."
                ),
            }

        now = self._now()
        intent = await self._intents.create(
            MCPUploadIntent(
                id=str(uuid.uuid4()),
                user_id=ctx.user_id,
                grant_id=ctx.grant_id,
                client_id=ctx.client_id,
                file_name=safe_name,
                extension=extension,
                max_bytes=max_bytes,
                folder_id=destination,
                target_format=normalized_target,
                status=MCPUploadIntentStatus.PENDING,
                created_at=now,
                expires_at=now + timedelta(seconds=self._ttl),
            )
        )
        capability = self._mint(intent.id, now)
        log_data_access(
            user_id=str(ctx.user_id),
            action="mcp.attachment_link_created",
            resource=f"mcp_upload:{intent.id}",
            grant_id=ctx.grant_id,
            client_id=ctx.client_id,
            extension=extension,
            max_bytes=max_bytes,
            capability_fingerprint=self.fingerprint(capability),
        )
        return CreatedUploadLink(
            intent=intent,
            capability=capability,
            upload_url=self._upload_url,
            expires_in_seconds=self._ttl,
        )

    # ------------------------------------------------------------------
    # Redemption
    # ------------------------------------------------------------------

    async def redeem_start(self, capability: str) -> MCPUploadIntent:
        """Claim the intent exactly once, after re-authorizing it.

        The grant is re-read and every fact recorded at creation is re-checked:
        still active, still holding ``documents.write`` (and ``documents.convert``
        when a conversion was requested), and still confined to a folder that
        contains the destination. Only then does the atomic
        ``PENDING → RECEIVING`` update run.
        """
        intent = await self._resolve_capability(capability)
        await self._assert_authorized(intent)
        redeemed = await self._intents.start_receiving(intent.id, now=self._now())
        if redeemed is None:
            raise UploadUnavailable()
        return redeemed

    async def inspect(self, capability: str) -> MCPUploadIntent:
        """Read an intent without consuming the capability.

        Used by the browser page to display the allowed size and type before the
        upload starts. It performs the same grant and expiry checks as
        ``redeem_start`` but never writes, so it cannot burn a link.
        """
        intent = await self._resolve_capability(capability)
        await self._assert_authorized(intent)
        if not intent.is_pending(self._now()):
            raise UploadUnavailable()
        return intent

    async def _resolve_capability(self, capability: str) -> MCPUploadIntent:
        if not capability or len(capability) > _MAX_CAPABILITY_LENGTH:
            raise UploadUnavailable()
        try:
            raw = self._fernet.decrypt_at_time(
                capability.encode("ascii"),
                ttl=self._ttl,
                current_time=int(self._clock()),
            )
            data = json.loads(raw)
            intent_id = data["intent_id"]
            if not isinstance(intent_id, str) or not intent_id:
                raise ValueError("Invalid capability payload")
        except (InvalidToken, ValueError, UnicodeError, KeyError, TypeError):
            raise UploadUnavailable() from None
        intent = await self._intents.get(intent_id)
        if intent is None:
            raise UploadUnavailable()
        return intent

    async def _assert_authorized(self, intent: MCPUploadIntent) -> None:
        """Fail closed on every fact the grant has to still support."""
        if intent.is_expired(self._now()):
            raise UploadUnavailable()
        grant = await self._grants.get_grant(intent.grant_id)
        if (
            grant is None
            or getattr(grant, "user_id", None) != intent.user_id
            or not grant.is_active()
            or not grant.covers(AgentScope.DOCUMENTS_WRITE)
        ):
            raise UploadUnavailable()
        if intent.target_format and not grant.covers(AgentScope.DOCUMENTS_CONVERT):
            raise UploadUnavailable()
        if grant.folder_access is FolderAccess.ALL:
            return
        if not folder_scope_is_usable(grant.folder_access, grant.folder_id):
            raise UploadUnavailable()
        assert grant.folder_id is not None  # guaranteed by folder_scope_is_usable
        subtree = await self._files.resolve_folder_scope(intent.user_id, grant.folder_id)
        if subtree is None or intent.folder_id not in subtree:
            raise UploadUnavailable()

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    async def complete(self, intent: MCPUploadIntent, file_id: str) -> MCPUploadIntent:
        """Record the landed file and, when asked, start the conversion.

        The conversion goes through the same code path the ``convert_file`` tool
        uses (injected as ``start_conversion``): origin MCP, ``grant_id`` set,
        the tier resolved from the database, credits charged as usual. A
        conversion failure is a *status* on the intent, not a lost upload — the
        file is already in the Drive and the row keeps both facts.
        """
        job_id: str | None = None
        if intent.target_format:
            if self._start_conversion is None:  # pragma: no cover - always wired
                error = "The file was saved, but the conversion is not available."
                await self._intents.mark_failed(intent.id, error=error)
                return replace(
                    intent,
                    status=MCPUploadIntentStatus.FAILED,
                    file_id=file_id,
                    error=error,
                )
            result = await self._start_conversion(
                replace(intent, file_id=file_id)
            )
            if not result.get("ok"):
                error = str(
                    result.get("error")
                    or "The file was saved, but the conversion could not be started."
                )
                await self._intents.mark_failed(intent.id, error=error)
                return replace(
                    intent,
                    status=MCPUploadIntentStatus.FAILED,
                    file_id=file_id,
                    error=error,
                )
            job = result.get("job") or {}
            candidate = job.get("job_id")
            if isinstance(candidate, str) and candidate:
                job_id = candidate
        await self._intents.mark_completed(intent.id, file_id=file_id, job_id=job_id)
        return replace(
            intent,
            status=MCPUploadIntentStatus.COMPLETED,
            file_id=file_id,
            job_id=job_id,
            error=None,
        )

    async def fail(self, intent_id: str, error: str) -> None:
        """Mark an upload failed with a user-safe sentence.

        Called by the endpoint when the streamed body, the storage write or the
        upload validation is refused, so ``get_upload_status`` reports the same
        failure the client saw.
        """
        await self._intents.mark_failed(intent_id, error=error)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self, ctx: MCPToolContext, upload_id: str) -> dict[str, Any]:
        """Owner- and grant-scoped status of one upload.

        Never returns a storage key, a capability or a presigned URL. A completed
        conversion's download link is minted by the *tool* (it is bound to the
        access token), not here.
        """
        denied = self._documents_required().require(
            ctx, AgentScope.DOCUMENTS_READ, "get_upload_status"
        )
        if denied:
            return denied
        intent = await self._intents.get(upload_id)
        if (
            intent is None
            or intent.user_id != ctx.user_id
            # A different connection of the same user must not see this upload:
            # the grant is the scope, exactly as it is for every other tool.
            or (ctx.grant_id and intent.grant_id != ctx.grant_id)
        ):
            return {"ok": False, "error": "No such upload was found."}

        result: dict[str, Any] = {
            "ok": True,
            "upload_id": intent.id,
            "status": intent.status.value,
            "file_name": intent.file_name,
            "extension": intent.extension,
            "expires_at": intent.expires_at.isoformat() if intent.expires_at else None,
            "file": None,
            "job": None,
            "error": intent.error,
        }
        if intent.file_id:
            file_result = await self._documents_required().get_file(ctx, intent.file_id)
            if file_result.get("ok"):
                result["file"] = file_result.get("file")
        if intent.job_id:
            job = await self._conversions.get_conversion_job(intent.job_id)
            if job is not None and job.user_id == ctx.user_id:
                result["job"] = _job_payload(job)
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), UTC)

    def _documents_required(self) -> _Documents:
        """The document toolbox, for the two methods that need it.

        Only the MCP tool path creates intents or reads status against the
        caller's folder confinement; the browser endpoint redeems and completes
        them, and deliberately does not carry the toolbox (and therefore cannot
        create one). Failing loudly here is better than silently skipping a
        folder check.
        """
        if self._documents is None:  # pragma: no cover - misconfiguration
            raise RuntimeError("The document toolbox is required for this operation.")
        return self._documents

    def _mint(self, intent_id: str, now: datetime) -> str:
        payload = json.dumps({"intent_id": intent_id}, separators=(",", ":")).encode()
        return self._fernet.encrypt_at_time(payload, int(now.timestamp())).decode()
