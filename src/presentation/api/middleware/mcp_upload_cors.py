"""CORS for the MCP attachment upload path only.

WHY this is not part of the app-wide ``CORSMiddleware``: that one is configured
with ``allow_credentials=True`` and an explicit origin allow-list, because the
SPA authenticates with a bearer token. The upload endpoint has no credentials at
all — the capability in the ``X-Transform-Upload`` header is the entire
authority — and it may be called from any origin (a client's sandbox, a local
tool, a browser page served elsewhere). Requiring an allow-list entry would make
a working link look broken for calls that are, by design, anonymous.

So this middleware opens exactly one path, and only there:

* ``Access-Control-Allow-Origin: *`` — no credentials, so the wildcard is valid;
* ``X-Transform-Upload`` is an allowed request header (the capability);
* ``PUT``, ``POST`` and ``OPTIONS`` are allowed methods;
* ``Access-Control-Allow-Credentials`` is REMOVED, because ``*`` together with
  credentials is invalid and browsers reject the response.

It is added last, so it runs outermost: the app-wide CORS middleware may already
have added its own origin header for a request from an allowed origin, and this
must replace it rather than emit a second value (two ``Allow-Origin`` headers is
also rejected by browsers).
"""

from collections.abc import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.application.services.mcp.upload_capability import UPLOAD_HEADER_NAME, UPLOAD_PATH

_ALLOW_HEADERS = f"{UPLOAD_HEADER_NAME}, Content-Type"
_ALLOW_METHODS = "PUT, POST, GET, OPTIONS"


class MCPUploadCORSMiddleware(BaseHTTPMiddleware):
    """Permit any origin to upload and to fetch the page, with no credentials."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path != UPLOAD_PATH:
            return await call_next(request)

        if request.method == "OPTIONS":
            # Answer the preflight here rather than letting the credentialed
            # global middleware answer it with an origin-specific policy.
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": _ALLOW_METHODS,
                    "Access-Control-Allow-Headers": _ALLOW_HEADERS,
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )

        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        # ``*`` with credentials is invalid, and the app-wide middleware sets
        # this for an allowed origin; the upload path never authenticates with a
        # cookie, so dropping it is both correct and required.
        if "access-control-allow-credentials" in response.headers:
            del response.headers["access-control-allow-credentials"]
        response.headers["Access-Control-Allow-Methods"] = _ALLOW_METHODS
        response.headers["Access-Control-Allow-Headers"] = _ALLOW_HEADERS
        response.headers["Vary"] = "Origin"
        return response
