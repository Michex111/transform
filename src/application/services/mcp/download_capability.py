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
from src.domain.security.value_object.agent_scope import AgentScope, is_subset

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


class _Folders(Protocol):
    async def resolve_folder_scope(self, user_id: int, folder_id: str) -> set[str] | None: ...


@dataclass(frozen=True)
class DownloadTarget:
    job_id: str
    user_id: int
    output_key: str
    grant_id: str


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
        target = await self._authorize(token_hash, job_id)
        payload = json.dumps({
            "token_hash": token_hash,
            "job_id": target.job_id,
            "output_hash": hashlib.sha256(target.output_key.encode()).hexdigest(),
        }, separators=(",", ":")).encode()
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
            token_hash, job_id, output_hash = (
                data["token_hash"], data["job_id"], data["output_hash"]
            )
            if not all(isinstance(value, str) and value for value in (token_hash, job_id, output_hash)):
                raise ValueError("Invalid capability payload")
        except (InvalidToken, ValueError, UnicodeError, KeyError, TypeError):
            raise DownloadUnavailable() from None
        target = await self._authorize(token_hash, job_id)
        actual = hashlib.sha256(target.output_key.encode()).hexdigest()
        if not hmac.compare_digest(actual, output_hash):
            raise DownloadUnavailable()
        return target

    async def _authorize(self, token_hash: str, job_id: str) -> DownloadTarget:
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
        if (
            grant is None or not grant.covers(AgentScope.DOCUMENTS_READ)
            or AgentScope.DOCUMENTS_READ not in record.scopes
            or not is_subset(record.scopes, grant.scopes)
            or record.client_id != grant.client_id
            or record.subject != str(grant.user_id)
        ):
            raise DownloadUnavailable()
        job = await self._conversions.get_conversion_job(job_id)
        if not is_job_owner(job, grant.user_id):
            raise DownloadUnavailable()
        assert job is not None
        if job.status is not JobStatus.COMPLETED or not job.output_file:
            raise DownloadUnavailable()
        if grant.folder_access is not FolderAccess.ALL:
            if grant.folder_access is not FolderAccess.FOLDER or not grant.folder_id or not job.object_key:
                raise DownloadUnavailable()
            subtree = await self._folders.resolve_folder_scope(grant.user_id, grant.folder_id)
            row = await self._files.find_by_key(grant.user_id, job.object_key)
            if subtree is None or row is None or row.folder_id not in subtree:
                raise DownloadUnavailable()
        return DownloadTarget(job.job_id, grant.user_id, job.output_file, grant.id)
