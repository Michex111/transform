"""Capture middleware for API request telemetry.

One place records every attributable public API request. Deliberately a single
centralised middleware rather than logging calls sprinkled through endpoints:
an endpoint that forgets to instrument itself is invisible in the dashboard, and
there is no way to notice the omission from the outside. Here, "not logged"
means "matched no route" or "was explicitly excluded", both of which are
auditable from this file alone.

## What is captured, and where attribution comes from

The account and API key are read from **request state written by the
authentication dependency**, not from a header. That dependency has already
verified the credential to produce the ``CurrentUser``; re-deriving the owner
here from something the caller sent would be an attribution forgery. A request
that never authenticated (a 401, a 429 from the rate limiter) has no owner and
is therefore **not recorded** — there is no account to attribute it to, and
inventing one would put another user's traffic in someone's dashboard.

## Exclusions, and why each is excluded

* ``/health``, ``/ready``, ``/metrics`` — infrastructure probes. Logging them
  would swamp a real account's history with platform noise.
* ``/docs``, ``/redoc``, ``/openapi.json`` — static documentation.
* ``/.well-known`` and ``/mcp`` — the OAuth/MCP transport. Those calls are
  captured, in richer form, as **MCP tool invocations** by the tool wrapper;
  logging the same activity twice under two identities would double-count it.
* ``/api/v1/developer`` — the dashboard's own reads. Measuring the instrument
  would make the chart describe itself, and every refresh would add rows.
* The job event stream and file download streams — long-lived. A "request" that
  lives for ten minutes contributes a ten-minute "duration" that describes the
  connection's lifetime rather than the server's work, and a handful of those
  would dominate every latency percentile on the dashboard. Recording them would
  make the p95 meaningless.

## Failure behaviour

Recording is fire-and-forget through the bounded ingestion queue. A full queue
drops the event and counts it; it never raises and never awaits. Telemetry
failure must not be able to fail the request it was describing.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import Request, Response

from src.infrastructure.config.settings import get_settings
from src.infrastructure.telemetry.ingestion import (
    TelemetryIngestion,
    get_telemetry_ingestion,
)

logger = logging.getLogger(__name__)

#: Keys the authentication dependency writes into ``request.state`` so this
#: middleware can attribute the request without repeating the authentication.
#: Defined here (rather than in the dependency) so the two sides share one
#: spelling and a rename cannot silently break attribution — it would simply
#: stop producing rows, which is a quiet failure mode.
STATE_ACCOUNT_ID = "telemetry_account_id"
STATE_API_KEY_ID = "telemetry_api_key_id"

#: Path prefixes that are never captured. See the module docstring for why each
#: is here; the list is intentionally short and explicit rather than a pattern,
#: so adding an exemption is a deliberate edit.
_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/mcp",
    "/.well-known",
    "/api/v1/developer",
    "/api/v1/events",
)

#: Suffixes for endpoints that stream a body and therefore hold the connection
#: open for the duration of a transfer rather than a computation.
_EXCLUDED_SUFFIXES: tuple[str, ...] = ("/stream",)

#: Sanity bound on a client-supplied correlation id. An ``X-Request-ID`` header
#: is echoed back and stored, so it must be length-bounded (a megabyte header
#: must not become a database column value) and restricted to printable ASCII
#: that cannot break a log line or an HTML attribute if a user copies it.
_MAX_REQUEST_ID_LENGTH = 80


def _is_excluded(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
        return True
    return any(path.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)


def _resolve_request_id(request: Request) -> str:
    """Use the client's correlation id when it is safe, else mint one.

    Honouring the incoming id (rather than always generating one) is what lets a
    developer correlate our row with their own client-side log; validating it
    first is what stops an arbitrary header value from being stored verbatim.
    """
    supplied = request.headers.get("x-request-id")
    if supplied:
        candidate = supplied.strip()
        if 0 < len(candidate) <= _MAX_REQUEST_ID_LENGTH and candidate.isascii() and candidate.isprintable():
            return candidate
    return f"req_{uuid.uuid4().hex[:24]}"


def _content_length(value: str | None) -> int | None:
    """Parse a Content-Length header, returning ``None`` when unusable.

    ``None`` means "not measured" and stays ``None`` all the way to the UI, so
    a chunked response is never rendered as a zero-byte one.
    """
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _ingestion() -> TelemetryIngestion:
    return get_telemetry_ingestion()


def _record(
    request: Request,
    *,
    request_id: str,
    status_code: int,
    duration_ms: float,
    response: Response | None,
) -> None:
    """Build and enqueue the event, swallowing every failure.

    Wrapped end to end on purpose: telemetry must never be able to fail — or
    change — the request it describes. `record_event` already promises not to
    raise, but a malformed attribution value or an unavailable settings read
    would surface here, and "this request was not logged" is the only acceptable
    degradation.
    """
    account_id = getattr(request.state, STATE_ACCOUNT_ID, None)
    if account_id is None:
        # Unauthenticated, refused before identity was established, or an
        # unmatched route. There is no owner, so there is no log row.
        return

    try:
        from src.domain.telemetry.entities.api_request_event import ApiRequestEvent

        route = request.scope.get("route")
        event = ApiRequestEvent(
            id=str(uuid.uuid4()),
            account_id=int(account_id),
            api_key_id=getattr(request.state, STATE_API_KEY_ID, None),
            request_id=request_id,
            timestamp=datetime.now(UTC),
            method=request.method,
            route_template=getattr(route, "path", None) or "unmatched",
            status_code=status_code,
            duration_ms=round(duration_ms, 3),
            environment=get_settings().ENVIRONMENT,
            request_bytes=_content_length(request.headers.get("content-length")),
            response_bytes=_content_length(response.headers.get("content-length")) if response else None,
        )
        _ingestion().record_event(event)
    except Exception:  # noqa: BLE001 — observability is never load-bearing
        logger.warning(
            "Telemetry capture failed for %s %s", request.method, request.url.path, exc_info=True
        )


async def api_telemetry_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Time the request, echo a correlation id, and record the event."""
    path = request.url.path

    if _is_excluded(path) or not path.startswith("/api/"):
        return await call_next(request)

    request_id = _resolve_request_id(request)
    # Make the id visible to handlers (and to a client-side retry log) without
    # threading it through every function signature.
    request.state.request_id = request_id

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # An unhandled exception. The client will get a 500 from the
        # ServerErrorMiddleware above us, so recording 500 is the honest
        # representation of what happened — and a failing request is exactly the
        # one a user needs in the log. The exception is re-raised untouched:
        # this middleware observes, it does not handle.
        _record(
            request,
            request_id=request_id,
            status_code=500,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            response=None,
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000.0
    response.headers["X-Request-ID"] = request_id
    _record(
        request,
        request_id=request_id,
        status_code=response.status_code,
        duration_ms=duration_ms,
        response=response,
    )
    return response
