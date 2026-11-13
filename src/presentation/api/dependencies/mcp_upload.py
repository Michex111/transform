"""Composition for the MCP attachment upload endpoint.

The endpoint is not an MCP tool call: it arrives as a plain HTTP request with a
capability header, possibly from a code sandbox's ``curl``. It therefore cannot
use ``open_mcp_scope`` for its own request (there is no OAuth token), but the
pipeline it drives must be the same one every other upload uses. This module
composes that pipeline over the request's database session:

* ``MCPUploadCapabilities`` — redeem the capability, complete the intent, and
  (when asked) start the conversion through the *tool* path;
* ``TransferService`` — reserve the upload session and verify it;
* ``FileService`` — the authoritative cap, quota, magic-byte and folder checks;
* the object-store adapter — the actual write (single PUT or multipart parts).

Only the parts a caller must not be able to substitute are kept distinct: the
capability is the sole authority, and no user id, storage key or bucket is read
from the request.
"""

import asyncio
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.files.service import FileService
from src.application.services.files.transfer_service import TransferService
from src.application.services.mcp.upload_capability import MCPUploadCapabilities
from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntent
from src.infrastructure.adapters.repository.sql_conversion_job_repo import (
    SQLConversionJobRepository,
)
from src.infrastructure.adapters.repository.sql_mcp_repo import SQLMCPRepository
from src.infrastructure.adapters.repository.sql_mcp_upload_intent_repo import (
    SQLMCPUploadIntentRepository,
)
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_db_session
from src.presentation.api.dependencies.service_dependencies import (
    get_file_service,
    get_minio_download_adapter,
    get_transfer_service,
)

#: Timeout for one presigned part PUT. A 64 MiB part on a slow consumer link can
#: take a while; this bounds a stuck socket without abandoning a legitimate
#: upload.
_PART_PUT_TIMEOUT_SECONDS = 120


def _put_part(url: str, data: bytes) -> str:
    """PUT one multipart part to its presigned URL; return the raw ETag.

    ``urllib`` rather than a new HTTP dependency: the URL is already signed by
    the storage adapter, the body is one bounded chunk, and the only value the
    caller needs is the ``ETag`` header that ``complete_multipart_upload``
    requires.
    """
    request = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(request, timeout=_PART_PUT_TIMEOUT_SECONDS) as response:  # noqa: S310 - presigned https URL
        etag = response.headers.get("ETag", "")
    if not etag:
        raise RuntimeError("The storage provider did not return an ETag for the part.")
    return etag


@dataclass
class MCPUploadEndpointServices:
    """The services one upload request runs against."""

    capabilities: MCPUploadCapabilities
    transfers: TransferService
    files: FileService
    object_store: MinioFileStorageAdapter
    #: Injected so a test can replace the network write with a capture. The
    #: default is the presigned-URL PUT above.
    put_part: Callable[[str, bytes], Awaitable[str]] = lambda url, data: asyncio.to_thread(
        _put_part, url, data
    )

    @property
    def multipart_threshold_bytes(self) -> int:
        return self.transfers.multipart_threshold_bytes


def build_mcp_upload_endpoint(
    *,
    session: AsyncSession,
    transfers: TransferService,
    files: FileService,
    object_store: MinioFileStorageAdapter,
) -> MCPUploadEndpointServices:
    settings = get_settings()

    async def start_conversion(intent: MCPUploadIntent) -> dict:
        """Start the post-upload conversion through the MCP tool path.

        A fresh ``open_mcp_scope`` (and therefore a fresh request-lifetime
        session) is used deliberately: the tool path is the authority for the
        granted scopes, folder binding and tier, and re-implementing it here
        would create a second place where those rules could drift.
        """
        from src.presentation.mcp.dependencies import open_mcp_scope
        from src.presentation.mcp.tools import tool_context_from_scope

        async with open_mcp_scope(intent.user_id, intent.grant_id) as scope:
            ctx = tool_context_from_scope(
                scope,
                user_id=intent.user_id,
                client_id=intent.client_id,
                grant_id=intent.grant_id,
            )
            return await scope.toolbox.convert_file(
                ctx, intent.file_id or "", intent.target_format or ""
            )

    capabilities = MCPUploadCapabilities(
        intents=SQLMCPUploadIntentRepository(session=session),
        grants=SQLMCPRepository(session=session),
        files=files,
        conversions=SQLConversionJobRepository(session=session),
        # The endpoint never creates an intent or reads a status, so it does not
        # carry the document toolbox (and cannot create an upload link).
        documents=None,
        secret=settings.SECRET_KEY.get_secret_value(),
        resource_url=settings.mcp_resource_url(),
        max_attachment_bytes=settings.MCP_ATTACHMENT_MAX_BYTES,
        ttl_seconds=settings.MCP_UPLOAD_LINK_TTL_SECONDS,
        start_conversion=start_conversion,
    )
    return MCPUploadEndpointServices(
        capabilities=capabilities,
        transfers=transfers,
        files=files,
        object_store=object_store,
    )


def get_mcp_upload_endpoint_services(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    transfers: Annotated[TransferService, Depends(get_transfer_service)],
    files: Annotated[FileService, Depends(get_file_service)],
    object_store: Annotated[MinioFileStorageAdapter, Depends(get_minio_download_adapter)],
) -> MCPUploadEndpointServices:
    return build_mcp_upload_endpoint(
        session=session, transfers=transfers, files=files, object_store=object_store
    )
