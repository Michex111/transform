"""Single-use HTTP upload endpoint for MCP chat attachments.

An MCP client's attachment never reaches this server: the model sees extracted
text and images, not the original bytes. ``create_upload_link`` mints a
capability, and this endpoint is where the real bytes arrive — either from the
client's code sandbox with ``curl`` (``PUT``, capability in a header) or from the
browser page served here (``POST`` multipart or ``PUT``).

Why the capability is never in the query string: a query string reaches access
logs, proxies and ``Referer`` headers. It travels in the ``X-Transform-Upload``
header, or — for the browser page only — in the URL *fragment*, which browsers
never send to the server, and which the page clears from history before the
upload starts.

Why this path is exempt from the API's body cap: the body is the file, and the
endpoint enforces its own streamed ceiling (``intent.max_bytes``) while counting
bytes, aborting with ``413`` the moment the total exceeds it. A ``Content-Length``
fast path still refuses an obviously oversized request before any byte is read.

Nothing here accepts a user id, a storage key or a bucket. The capability is the
only authority, and every refusal is the same sentence so the link cannot be
used as an oracle.
"""

import tempfile
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.datastructures import UploadFile

from src.application.exceptions.file_system_exceptions import (
    FileSizeLimitExceededError,
    FileSystemError,
)
from src.application.services.mcp.upload_capability import (
    UPLOAD_HEADER_NAME,
    UPLOAD_UNAVAILABLE_MESSAGE,
    MCPUploadCapabilities,
    UploadUnavailable,
)
from src.domain.security.enitities.mcp_upload_intent import MCPUploadIntent
from src.infrastructure.logging.audit import log_data_access
from src.presentation.api.dependencies.mcp_upload import (
    MCPUploadEndpointServices,
    get_mcp_upload_endpoint_services,
)

router = APIRouter(prefix="/api/v1/mcp/upload", include_in_schema=False)

_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
#: Chunk size for reading a request body. Small enough to abandon an oversized
#: stream promptly, large enough that a 100 MiB body is ~1600 iterations.
_CHUNK_BYTES = 64 * 1024
#: Longest multipart field name we will echo back; anything longer is a
#: malformed request rather than a file name.
_MAX_NAME_ECHO = 120

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Upload a file to Transform</title>
<style>
:root{color-scheme:light dark;--bg:#fafafa;--fg:#18181b;--muted:#52525b;--line:#e4e4e7;--accent:#4f46e5}
@media (prefers-color-scheme:dark){:root{--bg:#0b0b0f;--fg:#f4f4f5;--muted:#a1a1aa;--line:#27272a;--accent:#818cf8}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
main{width:100%;max-width:520px}h1{font-size:20px;margin:0 0 4px}p{margin:0 0 16px;color:var(--muted)}
#drop{position:relative;border:2px dashed var(--line);border-radius:12px;padding:32px 20px;text-align:center;color:var(--muted);transition:border-color .15s,background .15s;cursor:pointer}
#drop.over{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent)}
#drop strong{display:block;color:var(--fg);font-size:16px;margin-bottom:4px}
#meta{font-size:13px;margin-top:8px}
#bar{height:6px;border-radius:999px;background:var(--line);overflow:hidden;margin:18px 0 10px;display:none}
#fill{height:100%;width:0;background:var(--accent);transition:width .1s}
#status{font-size:14px;min-height:22px;margin:0}
#status.ok{color:#16a34a}#status.err{color:#dc2626}
input[type=file]{display:none}
</style></head><body><main>
<h1>Upload a file to Transform</h1>
<p>This link accepts one file and can be used once.</p>
<div id="drop" role="button" tabindex="0" aria-label="Choose a file or drop it here">
  <strong>Drop the file here</strong>
  <span>or click to choose it</span>
  <div id="meta">Checking this link&hellip;</div>
</div>
<input type="file" id="file">
<div id="bar"><div id="fill"></div></div>
<p id="status" role="status" aria-live="polite"></p>
</main>
<script src="/api/v1/mcp/upload/script.js"></script></body></html>"""

# Deliberately a separate, static script (not a module, not inline) so a strict
# Content-Security-Policy can be applied without `unsafe-inline`. Nothing below
# interpolates server text: the capability is read from the fragment and the
# only strings written into the document come from the server's JSON response,
# written with textContent.
_SCRIPT = r"""(() => {
  "use strict";
  const capability = window.location.hash.slice(1);
  history.replaceState(null, "", window.location.pathname);

  const drop = document.getElementById("drop");
  const input = document.getElementById("file");
  const meta = document.getElementById("meta");
  const status = document.getElementById("status");
  const bar = document.getElementById("bar");
  const fill = document.getElementById("fill");
  let busy = false;

  const say = (text, cls) => {
    status.textContent = text;
    status.className = cls || "";
  };
  const human = (bytes) => {
    if (!Number.isFinite(bytes)) return "";
    if (bytes >= 1024 * 1024) return Math.round(bytes / (1024 * 1024)) + " MB";
    if (bytes >= 1024) return Math.round(bytes / 1024) + " KB";
    return bytes + " bytes";
  };

  if (!capability) {
    say("This upload link is missing its capability.", "err");
    meta.textContent = "";
    return;
  }

  fetch("/api/v1/mcp/upload/info", {
    headers: { "X-Transform-Upload": capability },
    cache: "no-store",
  }).then(async (response) => {
    if (!response.ok) throw new Error("unavailable");
    return response.json();
  }).then((info) => {
    const type = info.extension ? "." + info.extension : "this file type";
    meta.textContent = "Allowed: " + type + " · up to " + human(info.max_bytes);
  }).catch(() => {
    meta.textContent = "";
    say("This upload link is unavailable or has expired.", "err");
  });

  const pick = () => { if (!busy) input.click(); };
  drop.addEventListener("click", pick);
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(); }
  });
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) upload(input.files[0]);
  });
  ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, () => {
    drop.classList.remove("over");
  }));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    if (busy) return;
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) upload(file);
  });

  function upload(file) {
    if (busy) return;
    busy = true;
    say("Uploading " + file.name + "…");
    bar.style.display = "block";
    fill.style.width = "0%";

    const request = new XMLHttpRequest();
    request.open("PUT", "/api/v1/mcp/upload", true);
    request.setRequestHeader("X-Transform-Upload", capability);
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) {
        fill.style.width = Math.round((event.loaded / event.total) * 100) + "%";
      }
    });
    request.addEventListener("load", () => {
      busy = false;
      let payload = {};
      try { payload = JSON.parse(request.responseText || "{}"); } catch (_) { payload = {}; }
      if (request.status >= 200 && request.status < 300 && payload.ok) {
        fill.style.width = "100%";
        say("Uploaded. You can return to your chat.", "ok");
        if (payload.job_id) {
          meta.textContent = "Conversion started. Its status will be reported in your chat.";
        } else {
          meta.textContent = "The file is in your Transform Drive.";
        }
      } else {
        say(payload.error || "The upload could not be completed.", "err");
      }
    });
    request.addEventListener("error", () => {
      busy = false;
      say("The upload failed. Check your connection and try again.", "err");
    });
    request.send(file);
  }
})();"""


class _UploadRejected(Exception):
    """A refusal that can be reported to the client verbatim."""
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class _Spool(Protocol):
    """The subset of ``tempfile.SpooledTemporaryFile`` used here.

    Typed structurally so the helpers do not depend on a concrete generic class
    whose annotation differs between Python versions.
    """

    def write(self, data: bytes, /) -> int: ...
    def read(self, size: int = -1, /) -> bytes: ...
    def seek(self, offset: int, whence: int = 0, /) -> int: ...


async def _redeem(
    services: MCPUploadEndpointServices, capability: str | None
) -> MCPUploadIntent:
    try:
        return await services.capabilities.redeem_start(capability or "")
    except UploadUnavailable:
        raise HTTPException(
            status_code=404, detail=UPLOAD_UNAVAILABLE_MESSAGE, headers=_HEADERS
        ) from None


async def _read_stream(request: Request, spool: _Spool, intent: MCPUploadIntent) -> int:
    """Copy the request body into ``spool``, counting bytes and capping them.

    The cap is enforced WHILE reading, not from ``Content-Length``: a chunked
    body declares nothing, and a lying length must not be able to make the
    server store more than the link allows.
    """
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > intent.max_bytes:
            raise _UploadRejected(
                "The file is larger than this upload link allows.", status_code=413
            )
        spool.write(chunk)
    return total


async def _read_upload_file(
    upload: UploadFile, spool: _Spool, intent: MCPUploadIntent
) -> int:
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > intent.max_bytes:
            raise _UploadRejected(
                "The file is larger than this upload link allows.", status_code=413
            )
        spool.write(chunk)
    return total


async def _store(
    services: MCPUploadEndpointServices,
    intent: MCPUploadIntent,
    spool: _Spool,
    total: int,
) -> MCPUploadIntent:
    """Run the received bytes through the ordinary upload pipeline.

    ``create_upload`` reserves the object key; the bytes are written once (or in
    parts, when the total crosses the multipart threshold); then
    ``verify_upload_completion`` and ``FileService.complete_upload`` apply the
    authoritative checks — tier per-file cap, account quota, magic bytes,
    extension and folder ownership. Nothing here duplicates or weakens them.
    """
    if total == 0:
        raise _UploadRejected("The uploaded file was empty.", status_code=400)

    multipart = total >= services.multipart_threshold_bytes
    try:
        session = await services.transfers.create_upload(
            intent.extension,
            str(intent.user_id),
            file_name=intent.file_name,
            folder_id=intent.folder_id,
            # Declaring the measured size switches the session to multipart at
            # or above the threshold; below it, ``None`` keeps the single-PUT
            # path (and avoids reserving a multipart upload never started).
            file_size=total if multipart else None,
        )
    except (FileSystemError, ValueError, OSError) as exc:
        raise _UploadRejected(f"The file could not be uploaded: {exc}") from exc

    try:
        if session.upload_mode == "multipart":
            part_count = session.part_count or 0
            # ``create_upload`` answers with an ``UploadResponse``; the part
            # URLs are minted from the stored ``UploadSession`` it represents.
            upload_session = await services.transfers.get_upload_session(session.upload_id)
            parts = await services.transfers.generate_part_urls(
                upload_session, list(range(1, part_count + 1))
            )
            uploaded: list[tuple[int, str]] = []
            for number, url in parts:
                chunk = spool.read(session.part_size_bytes or _CHUNK_BYTES)
                if not chunk:
                    raise _UploadRejected("The uploaded file was incomplete.")
                uploaded.append((number, await services.put_part(url, chunk)))
            upload = await services.transfers.verify_upload_completion(
                session.upload_id, uploaded
            )
        else:
            await services.object_store.put_object(session.object_key, spool.read())
            upload = await services.transfers.verify_upload_completion(session.upload_id)
        file_id = await services.files.complete_upload(intent.user_id, upload)
    except FileSizeLimitExceededError as exc:
        await services.transfers.delete_upload_session(session.upload_id)
        # Pass the FileService sentence through: it names the tier's cap, which
        # is the actionable part for the user.
        raise _UploadRejected(str(exc), status_code=413) from exc
    except FileSystemError as exc:
        await services.transfers.delete_upload_session(session.upload_id)
        raise _UploadRejected(str(exc)) from exc
    except _UploadRejected:
        await services.transfers.delete_upload_session(session.upload_id)
        raise
    except Exception as exc:  # noqa: BLE001 - storage/transfer failures
        await services.transfers.delete_upload_session(session.upload_id)
        raise _UploadRejected(
            "The file could not be uploaded because storage was unavailable.",
            status_code=503,
        ) from exc

    return await services.capabilities.complete(intent, file_id)


def _payload(intent: MCPUploadIntent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        # ``ok`` reflects the upload, not the optional conversion: a file that
        # landed in the Drive is a success even if the conversion then failed,
        # and the failure is reported separately so the user can retry it.
        "ok": intent.file_id is not None,
        "upload_id": intent.id,
        "file_id": intent.file_id,
        "job_id": intent.job_id,
        "status": intent.status.value,
    }
    if intent.error:
        payload["error"] = intent.error
    return payload


def _audit(intent: MCPUploadIntent, *, size_bytes: int, capability: str) -> None:
    log_data_access(
        user_id=str(intent.user_id),
        action="mcp.attachment_upload",
        resource=f"mcp_upload:{intent.id}",
        grant_id=intent.grant_id,
        client_id=intent.client_id,
        extension=intent.extension,
        size_bytes=size_bytes,
        capability_fingerprint=MCPUploadCapabilities.fingerprint(capability),
    )


async def _receive(
    services: MCPUploadEndpointServices,
    intent: MCPUploadIntent,
    spool: _Spool,
    total: int,
    capability: str,
) -> JSONResponse:
    _audit(intent, size_bytes=total, capability=capability)
    try:
        completed = await _store(services, intent, spool, total)
    except _UploadRejected as exc:
        await services.capabilities.fail(intent.id, exc.message)
        return JSONResponse(
            {"ok": False, "error": exc.message}, status_code=exc.status_code, headers=_HEADERS
        )
    return JSONResponse(_payload(completed), headers=_HEADERS)


@router.put("")
async def receive_upload(
    request: Request,
    services: Annotated[
        MCPUploadEndpointServices, Depends(get_mcp_upload_endpoint_services)
    ],
) -> JSONResponse:
    """Receive one file as the raw request body.

    The capability is read from ``X-Transform-Upload`` only. The body is
    streamed to a spooled temporary file (memory below the multipart threshold,
    disk above it) so the cap can be enforced before anything is stored.
    """
    capability = request.headers.get(UPLOAD_HEADER_NAME, "")
    intent = await _redeem(services, capability)
    with tempfile.SpooledTemporaryFile(
        max_size=services.multipart_threshold_bytes
    ) as spool:
        try:
            total = await _read_stream(request, spool, intent)
        except _UploadRejected as exc:
            await services.capabilities.fail(intent.id, exc.message)
            return JSONResponse(
                {"ok": False, "error": exc.message},
                status_code=exc.status_code,
                headers=_HEADERS,
            )
        except Exception:  # noqa: BLE001 - a dropped connection is a failed upload
            await services.capabilities.fail(
                intent.id, "The upload was interrupted before it finished."
            )
            return JSONResponse(
                {"ok": False, "error": "The upload was interrupted before it finished."},
                status_code=400,
                headers=_HEADERS,
            )
        spool.seek(0)
        return await _receive(services, intent, spool, total, capability)


@router.post("")
async def receive_upload_form(
    request: Request,
    services: Annotated[
        MCPUploadEndpointServices, Depends(get_mcp_upload_endpoint_services)
    ],
) -> JSONResponse:
    """Receive one file as ``multipart/form-data`` field ``file``.

    Provided for the browser page (and for any client whose HTTP stack cannot
    stream a raw PUT). It shares the same redemption, cap and storage path as
    the PUT form.
    """
    capability = request.headers.get(UPLOAD_HEADER_NAME, "")
    intent = await _redeem(services, capability)
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - malformed multipart is a client error
        await services.capabilities.fail(intent.id, "The upload was malformed.")
        return JSONResponse(
            {"ok": False, "error": "The upload was malformed."},
            status_code=400,
            headers=_HEADERS,
        )
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        await services.capabilities.fail(intent.id, "No file was included in the upload.")
        return JSONResponse(
            {"ok": False, "error": "No file was included in the upload."},
            status_code=400,
            headers=_HEADERS,
        )
    with tempfile.SpooledTemporaryFile(
        max_size=services.multipart_threshold_bytes
    ) as spool:
        try:
            total = await _read_upload_file(upload, spool, intent)
        except _UploadRejected as exc:
            await services.capabilities.fail(intent.id, exc.message)
            return JSONResponse(
                {"ok": False, "error": exc.message},
                status_code=exc.status_code,
                headers=_HEADERS,
            )
        spool.seek(0)
        return await _receive(services, intent, spool, total, capability)


@router.get("/info")
async def upload_info(
    request: Request,
    services: Annotated[
        MCPUploadEndpointServices, Depends(get_mcp_upload_endpoint_services)
    ],
) -> JSONResponse:
    """Describe the link for the browser page, without consuming it.

    Read-only on purpose: the page calls this to show "allowed size and type"
    before the user chooses a file, and a dry run must never burn a single-use
    capability.
    """
    capability = request.headers.get(UPLOAD_HEADER_NAME, "")
    try:
        intent = await services.capabilities.inspect(capability)
    except UploadUnavailable:
        raise HTTPException(
            status_code=404, detail=UPLOAD_UNAVAILABLE_MESSAGE, headers=_HEADERS
        ) from None
    return JSONResponse(
        {
            "ok": True,
            "file_name": intent.file_name[:_MAX_NAME_ECHO],
            "extension": intent.extension,
            "max_bytes": intent.max_bytes,
            "expires_at": intent.expires_at.isoformat() if intent.expires_at else None,
        },
        headers=_HEADERS,
    )


@router.get("", response_class=HTMLResponse)
async def upload_page() -> HTMLResponse:
    return HTMLResponse(_PAGE, headers=_HEADERS)


@router.get("/script.js")
async def upload_script() -> Response:
    return Response(_SCRIPT, media_type="application/javascript", headers=_HEADERS)
