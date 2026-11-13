"""Browser redemption of a fragment-carried MCP download capability."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from src.application.services.mcp.download_capability import DownloadUnavailable, MCPDownloadCapabilities
from src.infrastructure.adapters.security.encryption import FileEncryptionService
from src.infrastructure.adapters.storage.minio_storage_adapter import MinioFileStorageAdapter
from src.infrastructure.logging.audit import log_data_access
from src.presentation.api.dependencies.download_stream import iter_decrypted_object
from src.presentation.api.dependencies.mcp_download import get_mcp_download_capabilities
from src.presentation.api.dependencies.service_dependencies import (
    get_encryption_service,
    get_minio_download_adapter,
)
from src.presentation.api.http_headers import content_disposition_attachment

router = APIRouter(prefix="/api/v1/mcp/download", include_in_schema=False)
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Download conversion</title></head>
<body><p id="message">Preparing your download. This link requires JavaScript.</p>
<form id="download" method="post" action="/api/v1/mcp/download/redeem">
<input type="hidden" name="capability" id="capability"></form>
<script src="/api/v1/mcp/download/script.js" defer></script></body></html>"""

# No untrusted text is interpolated into the document or script. URL fragments
# are not sent to the server, proxies, or HTTP access logs. Clear it from browser
# history before submitting the form body to the same origin.
_SCRIPT = """(() => {
  const token = window.location.hash.slice(1);
  history.replaceState(null, "", window.location.pathname);
  if (!token) {
    document.getElementById("message").textContent = "This download link is missing its capability.";
    return;
  }
  document.getElementById("capability").value = token;
  document.getElementById("message").textContent = "Starting your download.";
  document.getElementById("download").submit();
})();"""


@router.get("", response_class=HTMLResponse)
async def download_page() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers=_HEADERS)


@router.get("/script.js")
async def download_script() -> Response:
    return Response(_SCRIPT, media_type="application/javascript", headers=_HEADERS)


@router.post("/redeem")
async def redeem_download(
    capability: Annotated[str, Form()],
    service: Annotated[MCPDownloadCapabilities, Depends(get_mcp_download_capabilities)],
    storage: Annotated[MinioFileStorageAdapter, Depends(get_minio_download_adapter)],
    encryption: Annotated[FileEncryptionService | None, Depends(get_encryption_service)],
) -> StreamingResponse:
    try:
        target = await service.redeem(capability)
    except DownloadUnavailable:
        raise HTTPException(status_code=404, detail="Download unavailable or expired.", headers=_HEADERS) from None
    log_data_access(
        user_id=str(target.user_id),
        action="download",
        resource="conversion_output" if target.job_id else "drive_file",
        job_id=target.job_id,
        file_id=target.file_id,
        grant_id=target.grant_id,
    )
    name = target.file_name or target.output_key.rsplit("/", 1)[-1]
    return StreamingResponse(
        iter_decrypted_object(storage, target.output_key, encryption, str(target.user_id)),
        media_type="application/octet-stream",
        headers={
            **_HEADERS,
            "Content-Disposition": content_disposition_attachment(name),
            "X-Content-Type-Options": "nosniff",
        },
    )
