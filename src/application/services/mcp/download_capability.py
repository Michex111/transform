"""Short-lived, revocable browser capabilities for MCP conversion outputs."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from src.application.services.mcp.access_service import hash_secret
from src.domain.conversions.entities.conversion_job import ConversionJob
from src.domain.conversions.policies.job_ownership import is_job_owner
from src.domain.conversions.value_object.job_status import JobStatus
from src.domain.security.value_object.agent_access_scope import FolderAccess
from src.domain.security.value_object.agent_scope import AgentScope

DOWNLOAD_TTL_SECONDS = 300


class DownloadUnavailable(Exception):
    """The capability or its underlying authorization is unavailable."""


class _Repository(Protocol):
    async def get_token(self, token_hash: str) -> Any: ...
    async def get_grant(self, grant_id: str) -> Any: ...


class _Conversions(Protocol):
    async def get_conversion_job(self, job_id: str) -> ConversionJob | None: ...


class _Files(Protocol):
    async def find_by_key(self, user_id: int, file_key: str) -> Any: ...
    async def get_file(self, user_id: int, file_id: str) -> Any: ...


class _Folders(Protocol):
    async def resolve_folder_scope(self, user_id: int, folder_id: str) -> set[str] | None: ...


@dataclass(frozen=True)
class DownloadTarget:
    """What a redeemed capability resolves to.

    ``file_name`` is carried so the redemption handler can set a
    Content-Disposition without re-reading the row, and so a Drive-file link and
    a conversion-output link share one shape.
    """

    job_id: str | None
    user_id: int
    output_key: str
    grant_id: str
    file_name: str | None = None
    file_id: str | None = None


class MCPDownloadCapabilities:
    """Authorize on issue and redemption; never put the OAuth token in the URL."""

    def __init__(
        self, *, repository: _Repository, conversions: _Conversions, files: _Files,
        folders: _Folders, secret: str, resource_url: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._conversions = conversions
        self._files = files
        self._folders = folders
        self._resource = resource_url
        parts = urlsplit(resource_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("Invalid MCP resource URL")
        self._landing = f"{parts.scheme}://{parts.netloc}/api/v1/mcp/download"
        # Domain-separated from every other use of SECRET_KEY.
        key = hmac.new(secret.encode(), b"mcp-download-capability-v1", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))
        self._clock = clock

    async def create_link(self, access_token: str, job_id: str) -> dict[str, Any]:
        token_hash = hash_secret(access_token)
        target = await self._authorize(token_hash, job_id=job_id)
        payload = json.dumps({
            "kind": "job",
            "token_hash": token_hash,
            "job_id": target.job_id,
            "output_hash": hashlib.sha256(target.output_key.encode()).hexdigest(),
        }, separators=(",", ":")).encode()
        return self._link(payload)

    async def create_file_link(self, access_token: str, file_id: str) -> dict[str, Any]:
        """A short-lived download capability for one of the user's Drive files.

        Same shape and same rechecks as a conversion-output link, because the
        risk is identical: the URL is a bearer capability, so authorization
        happens when it is *issued* and again when it is *redeemed*. Without this,
        an agent could hand a user a link for a completed conversion but not for
        the file that conversion started from.
        """
        token_hash = hash_secret(access_token)
        target = await self._authorize(token_hash, file_id=file_id)
        payload = json.dumps({
            "kind": "file",
            "token_hash": token_hash,
            "file_id": target.file_id,
            "output_hash": hashlib.sha256(target.output_key.encode()).hexdigest(),
        }, separators=(",", ":")).encode()
        return self._link(payload)

    def _link(self, payload: bytes) -> dict[str, Any]:
        capability = self._fernet.encrypt_at_time(payload, int(self._clock())).decode()
        return {
            "download_url": f"{self._landing}#{capability}",
            "expires_in_seconds": DOWNLOAD_TTL_SECONDS,
        }

    async def redeem(self, capability: str) -> DownloadTarget:
        if not capability or len(capability) > 4096:
            raise DownloadUnavailable()
        try:
            raw = self._fernet.decrypt_at_time(
                capability.encode("ascii"), ttl=DOWNLOAD_TTL_SECONDS,
                current_time=int(self._clock()),
            )
            data = json.loads(raw)
            token_hash = data["token_hash"]
            output_hash = data["output_hash"]
            kind = data.get("kind", "job")
            if not all(
                isinstance(value, str) and value
                for value in (token_hash, output_hash, kind)
            ):
                raise ValueError("Invalid capability payload")
        except (InvalidToken, ValueError, UnicodeError, KeyError, TypeError):
            raise DownloadUnavailable() from None
        if kind == "file":
            file_id = data.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise DownloadUnavailable()
            target = await self._authorize(token_hash, file_id=file_id)
        else:
            job_id = data.get("job_id")
            if not isinstance(job_id, str) or not job_id:
                raise DownloadUnavailable()
            target = await self._authorize(token_hash, job_id=job_id)
        actual = hashlib.sha256(target.output_key.encode()).hexdigest()
        if not hmac.compare_digest(actual, output_hash):
            raise DownloadUnavailable()
        return target

    async def _authorize(
        self, token_hash: str, *, job_id: str | None = None, file_id: str | None = None
    ) -> DownloadTarget:
        record = await self._repository.get_token(token_hash)
        now = datetime.fromtimestamp(self._clock(), UTC)
        if record is None or record.kind != "ACCESS" or record.revoked_at is not None:
            raise DownloadUnavailable()
        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= now or (record.resource is not None and record.resource != self._resource):
            raise DownloadUnavailable()
        grant = await self._repository.get_grant(record.grant_id)
        # The GRANT's scopes are the authority (matching ``load_access_token``).
        # The token's frozen copy is not checked against it, because an edit on
        # the Connected-apps screen must take effect immediately in both
        # directions; what matters is that the grant still permits reading and
        # that the token belongs to that grant's client and subject.
        if (
            grant is None
            or not grant.is_active()
            or not grant.covers(AgentScope.DOCUMENTS_READ)
            or record.client_id != grant.client_id
            or record.subject != str(grant.user_id)
        ):
            raise DownloadUnavailable()

        if file_id is not None:
            try:
                row = await self._files.get_file(grant.user_id, file_id)
            except Exception:  # noqa: BLE001 - a foreign/missing row is the same answer
                raise DownloadUnavailable() from None
            if row is None or not getattr(row, "file_key", None):
                raise DownloadUnavailable()
            await self._assert_folder(grant, getattr(row, "folder_id", None))
            return DownloadTarget(
                job_id=None,
                user_id=grant.user_id,
                output_key=row.file_key,
                grant_id=grant.id,
                file_name=getattr(row, "file_name", None),
                file_id=getattr(row, "id", file_id),
            )

        job = await self._conversions.get_conversion_job(job_id or "")
        if not is_job_owner(job, grant.user_id):
            raise DownloadUnavailable()
        assert job is not None
        if job.status is not JobStatus.COMPLETED or not job.output_file:
            raise DownloadUnavailable()
        if grant.folder_access is not FolderAccess.ALL:
            if grant.folder_access is not FolderAccess.FOLDER or not grant.folder_id or not job.object_key:
                raise DownloadUnavailable()
            row = await self._files.find_by_key(grant.user_id, job.object_key)
            if row is None:
                raise DownloadUnavailable()
            await self._assert_folder(grant, row.folder_id)
        return DownloadTarget(
            user_id=grant.user_id,
            output_key=job.output_file,
            grant_id=grant.id,
            file_name=job.output_file.rsplit("/", 1)[-1],
            job_id=job.job_id,
        )

    async def _assert_folder(self, grant: Any, folder_id: str | None) -> None:
        """Refuse a target outside a confined grant's subtree.

        Shares the subtree rule with every document tool: resolving the bound
        folder returns ``None`` when it is missing or not the caller's, which is
        a denial rather than "unrestricted".
        """
        if grant.folder_access is FolderAccess.ALL:
            return
        if grant.folder_access is not FolderAccess.FOLDER or not grant.folder_id:
            raise DownloadUnavailable()
        subtree = await self._folders.resolve_folder_scope(grant.user_id, grant.folder_id)
        if subtree is None or folder_id not in subtree:
            raise DownloadUnavailable()
