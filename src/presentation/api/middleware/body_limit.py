"""Reject oversized request bodies before they are read.

WHY THIS EXISTS
Every other limit in this service is per-field (pydantic `max_length`) or
per-route (the webhook's own 1 MiB pre-check, the avatar's 2 MiB check). Those
bound the *content*, not the *transport*: without this, any client can declare
and stream an arbitrarily large body — a few hundred MB, or simply an endless
chunked stream — and make uvicorn buffer it in memory before a single schema
ever sees it. That is a denial-of-service primitive that costs the attacker
nothing, and it needs no valid credentials to start (the body is read before the
route's dependencies authenticate).

It rejects on the declared `Content-Length`, so the refusal happens before any
byte of the body is consumed.

THE CHUNKED TRADE-OFF (deliberate)
A `Transfer-Encoding: chunked` request declares no length, so this middleware
cannot refuse it up front. Reading-and-counting would defeat the point (the
memory is already spent by the time the count exceeds the cap) and would mean
buffering the very body we are trying to avoid buffering. Chunked requests are
therefore left to the per-schema field limits, and to the platform in front of
this service (Render's proxy enforces its own request limits). The common
attack — and every mainstream HTTP client — sends `Content-Length`, which is
what this closes.
"""

from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.config.settings import get_settings

#: Only `/api/…` is capped. The non-API paths are the health probes and
#: `/metrics`, which take no body at all, and being permissive there keeps this
#: middleware from being able to break a platform health check.
_CAPPED_PREFIX = "/api/"


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Answer 413 when a request declares a body larger than the configured cap."""

    def __init__(self, app, max_bytes: int | None = None) -> None:
        super().__init__(app)
        # Injected (rather than read lazily) so a test can pin a small cap
        # without touching the environment, and so the value cannot change
        # between the check and the response.
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        """The effective cap, resolved from settings on first use."""
        if self._max_bytes is None:
            self._max_bytes = get_settings().MAX_REQUEST_BODY_BYTES
        return self._max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith(_CAPPED_PREFIX):
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    length = int(declared)
                except ValueError:
                    # A malformed Content-Length is not something a conforming
                    # client sends; treat it as unparseable and let the server's
                    # own parser reject it rather than inventing a policy here.
                    length = -1
                if length > self.max_bytes:
                    # The same structured detail shape the rest of the API uses
                    # for a rejection the client can act on.
                    return Response(
                        status_code=413,
                        media_type="application/json",
                        content=(
                            '{"detail":{"code":"REQUEST_TOO_LARGE","message":'
                            '"The request body is larger than this API accepts."}}'
                        ),
                    )
        return await call_next(request)
