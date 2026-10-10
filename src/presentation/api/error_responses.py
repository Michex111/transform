"""Canonical error payloads for failures that are not a domain error.

Two distinct problems are solved here, and they are related.

**One mapping for an upstream provider failure.** The assistant's model provider
can fail in two ways that need different answers: *transiently* (throttled, or
briefly down — retrying is the right advice) or by *refusing* the request (a
retired model name, a revoked key — retrying will never help). Mapping those
per-endpoint is how `/chat` came to answer a throttled provider with a clean
"busy, try again" while `/summarize` and `/recommend` answered the *same*
condition with a bare 500. They are mapped once, on the application, so a new
assistant endpoint inherits the behaviour instead of having to remember it.

**CORS on an unhandled 500.** Starlette handles an unexpected exception in
``ServerErrorMiddleware``, which sits *outside* ``CORSMiddleware``. The browser
therefore never receives ``Access-Control-Allow-Origin`` on that response, and
reports the whole thing as a network failure — the user is shown
"failed to fetch" rather than the error. That is what made this bug hard to
report: the real cause was a 500 in the request log, but the page said the
network was down. This handler is the only place that can add the header: by the
time it runs, the CORS middleware has already unwound, so nothing downstream can
add it.
"""

from collections.abc import Sequence

from fastapi import Request
from fastapi.responses import JSONResponse

#: The provider is throttling us or briefly failing, and our own retries are
#: spent. 503 rather than 500: the request was fine and retrying is correct.
AI_BUSY_CODE = "AI_BUSY"
AI_BUSY_MESSAGE = "The assistant is busy right now. Please try again in a moment."

#: The provider refused the request outright, so a retry would fail identically.
#: 502: we are a gateway to a service that rejected us, and the fault is upstream
#: (configuration) rather than in what the caller sent.
AI_PROVIDER_ERROR_CODE = "AI_PROVIDER_ERROR"
AI_PROVIDER_ERROR_MESSAGE = (
    "The assistant could not complete that request. Please try again later."
)

#: Deliberately vague: an unhandled exception is a bug, and its message may quote
#: internals. The detail belongs in the log, not in the response.
INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "Something went wrong on our side. Please try again."


def _detail(code: str, message: str) -> dict[str, dict[str, str]]:
    """The ``{"detail": {"code", "message"}}`` shape the SPA already parses.

    Matching the shape FastAPI produces for an ``HTTPException`` is what lets the
    frontend show these through its existing error path instead of needing a
    special case per status.
    """
    return {"detail": {"code": code, "message": message}}


def assistant_busy_response() -> JSONResponse:
    """503 for a transient provider failure."""
    return JSONResponse(
        status_code=503, content=_detail(AI_BUSY_CODE, AI_BUSY_MESSAGE)
    )


def assistant_provider_error_response() -> JSONResponse:
    """502 for a provider that refused the request."""
    return JSONResponse(
        status_code=502, content=_detail(AI_PROVIDER_ERROR_CODE, AI_PROVIDER_ERROR_MESSAGE)
    )


def internal_error_response(
    request: Request, allowed_origins: Sequence[str]
) -> JSONResponse:
    """500 for an unhandled exception, with CORS headers added by hand.

    The headers are not a convenience. Without them the browser discards the
    response entirely, so the user sees a network error and the log's 500 is
    unreachable from the report. ``Vary: Origin`` is set for the same reason the
    CORS middleware sets it: the body is identical for every origin but the
    headers are not, so a shared cache must not reuse one origin's response for
    another.
    """
    response = JSONResponse(
        status_code=500, content=_detail(INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE)
    )
    origin = request.headers.get("origin")
    if origin and origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response
