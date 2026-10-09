"""Developer observability API: API Logs and MCP Activity.

Two product surfaces under one router, deliberately kept separate in their
semantics even though they share primitives (pagination, ranges, the metric
aggregator):

* **API Logs** — requests made against the public API with the account's
  credentials.
* **MCP Activity** — tool calls made by AI agents the account has authorized,
  plus the pause/resume/revoke controls that govern them.

Security posture, applied uniformly:

* Every endpoint depends on ``CurrentUser`` and scopes every query by
  ``int(current_user.id)``. There is no endpoint that accepts an account or
  owner from the request.
* Ranges are a fixed preset ladder, and the page size is clamped server-side, so
  a caller cannot ask for an unbounded scan.
* Mutations return the authoritative post-change connection, so the UI never
  has to guess whether the change took effect.
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from src.application.exceptions.mcp_exceptions import MCPAccessError
from src.application.ports.api_telemetry_port import (
    ApiLogFilters,
    McpActivityFilters,
)
from src.application.services.api_telemetry_service import (
    RANGE_PRESETS,
    ApiTelemetryService,
    InvalidRangeError,
    ResolvedRange,
    resolve_range,
)
from src.application.services.mcp_access_service import MCPAccessService
from src.application.services.mcp_activity_service import (
    McpActivityService,
    McpConnectionView,
)
from src.domain.security.value_object.agent_scope import (
    DESTRUCTIVE_SCOPES,
    SCOPE_DESCRIPTIONS,
    AgentScope,
)
from src.domain.telemetry.value_object.request_metrics import METRIC_META, ApiMetric
from src.infrastructure.adapters.repository.sql_api_telemetry_repo import SQLTelemetryRepository
from src.infrastructure.config.settings import get_settings
from src.infrastructure.database.session import get_session_factory
from src.presentation.api.dependencies.auth_dependencies import CurrentUser
from src.presentation.api.dependencies.service_dependencies import (
    get_api_telemetry_service,
    get_mcp_access_service,
    get_mcp_activity_service,
)
from src.presentation.schemas.developer import (
    ApiLogDetailResponse,
    ApiLogEntry,
    ApiLogListResponse,
    ApiMetricsResponse,
    McpActivityEntry,
    McpActivityListResponse,
    McpActivitySummaryResponse,
    McpConnectionListResponse,
    McpConnectionResponse,
    McpControlResponse,
    McpPermissionResponse,
    MetricsBucketResponse,
    MetricsOverviewResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/developer", tags=["developer"])

TelemetryService = Annotated[ApiTelemetryService, Depends(get_api_telemetry_service)]
McpActivity = Annotated[McpActivityService, Depends(get_mcp_activity_service)]
McpAccess = Annotated[MCPAccessService, Depends(get_mcp_access_service)]

#: Human sentences for a status code. This is a *derived* description of the
#: status, not a recorded response body — the API records none, and saying so is
#: why the field is called `status_meaning` rather than `response_summary`.
_STATUS_MEANINGS: dict[int, str] = {
    200: "Request completed successfully.",
    201: "The request created a resource.",
    202: "The request was accepted and is being processed.",
    204: "The request completed with no content to return.",
    206: "Partial content returned.",
    301: "The request was redirected.",
    302: "The request was redirected.",
    304: "The cached response is still valid.",
    400: "The request was rejected as invalid.",
    401: "The request was not authenticated.",
    403: "The account is not permitted to perform this action.",
    404: "The requested resource was not found.",
    405: "The method is not allowed on this endpoint.",
    409: "The request conflicted with the current state.",
    413: "The request payload was too large.",
    415: "The request payload type is not supported.",
    422: "The request failed validation.",
    429: "The request was rate limited.",
    500: "The server encountered an error handling the request.",
    502: "An upstream service returned an invalid response.",
    503: "The service is temporarily unavailable.",
    504: "An upstream service timed out.",
}


def _status_meaning(status_code: int) -> str:
    if status_code in _STATUS_MEANINGS:
        return _STATUS_MEANINGS[status_code]
    if status_code < 400:
        return "The request completed."
    if status_code < 500:
        return "The request was rejected."
    return "The server failed to handle the request."


def _parse_metric(value: str) -> ApiMetric:
    try:
        return ApiMetric(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_METRIC",
                "message": f"Unknown metric {value!r}.",
                "allowed": [metric.value for metric in ApiMetric],
            },
        ) from None


def _resolve_range(service: ApiTelemetryService, range_key: str | None) -> ResolvedRange:
    try:
        return service.resolve_range(range_key)
    except InvalidRangeError as exc:
        raise _invalid_range(exc) from exc


def _invalid_range(exc: InvalidRangeError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "INVALID_RANGE",
            "message": str(exc),
            "allowed": sorted(RANGE_PRESETS),
        },
    )


def _range_from_settings(range_key: str | None) -> ResolvedRange:
    """Resolve a range without a repository-bound service.

    The MCP endpoints and the SSE stream do not need the telemetry service's
    repository to decide a window, and the stream deliberately builds a fresh
    repository per tick — so they resolve the range from settings directly.
    """
    try:
        return resolve_range(
            range_key, max_range_days=get_settings().TELEMETRY_MAX_RANGE_DAYS
        )
    except InvalidRangeError as exc:
        raise _invalid_range(exc) from exc


def get_stream_session_factory():
    """The session factory the live stream uses, one session per tick.

    A FastAPI dependency (rather than a direct ``get_session_factory()`` call)
    so tests can point the stream at their own database: without the seam, a
    live-stream test would reach the process-wide engine and the real database
    while the rest of the test ran against SQLite. That mismatch is the kind of
    thing that passes locally and fails in CI, or worse, writes to a real one.
    """
    return get_session_factory()


def _page_size(value: int | None) -> int:
    settings = get_settings()
    if value is None:
        return settings.TELEMETRY_DEFAULT_PAGE_SIZE
    return max(1, min(value, settings.TELEMETRY_MAX_PAGE_SIZE))


def _event_filters(
    api_key_id: str | None,
    method: str | None,
    status_filter: str | None,
    route: str | None,
    request_id: str | None,
    outcome: str | None,
) -> ApiLogFilters:
    if outcome is not None and outcome not in ("success", "error"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_OUTCOME", "message": "outcome must be 'success' or 'error'."},
        )
    if method is not None and not method.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_METHOD", "message": "method must be an HTTP method name."},
        )
    return ApiLogFilters(
        api_key_id=api_key_id,
        method=method,
        status=status_filter,
        route_query=route,
        request_id=request_id,
        outcome=outcome,
    )


def _log_entry(event) -> ApiLogEntry:
    return ApiLogEntry(
        id=event.id,
        timestamp=event.timestamp,
        method=event.method,
        route=event.route_template,
        status_code=event.status_code,
        outcome="error" if event.is_error else "success",
        duration_ms=event.duration_ms,
        request_id=event.request_id,
        api_key_id=event.api_key_id,
        api_key_name=event.api_key_name,
        environment=event.environment,
        request_bytes=event.request_bytes,
        response_bytes=event.response_bytes,
    )


def _bucket_response(bucket) -> MetricsBucketResponse:
    return MetricsBucketResponse(
        t=bucket.start,
        count=bucket.count,
        success=bucket.success,
        errors=bucket.errors,
        rate=round(bucket.rate, 4),
        error_rate=round(bucket.error_rate, 4),
        avg_latency_ms=round(bucket.avg_latency_ms, 3) if bucket.avg_latency_ms is not None else None,
        p50_ms=round(bucket.p50_ms, 3) if bucket.p50_ms is not None else None,
        p95_ms=round(bucket.p95_ms, 3) if bucket.p95_ms is not None else None,
        p99_ms=round(bucket.p99_ms, 3) if bucket.p99_ms is not None else None,
    )


def _metrics_response(window, metrics) -> ApiMetricsResponse:
    overview = metrics.overview
    label, unit = METRIC_META[metrics.metric]
    return ApiMetricsResponse(
        range_key=window.key,
        start=metrics.start,
        end=metrics.end,
        bucket_seconds=metrics.bucket_seconds,
        metric=metrics.metric.value,
        metric_label=label,
        metric_unit=unit,
        generated_at=metrics.generated_at or datetime.now(UTC),
        overview=MetricsOverviewResponse(
            requests=overview.requests,
            success=overview.success,
            errors=overview.errors,
            success_rate=round(overview.success_rate, 3),
            error_rate=round(overview.error_rate, 3),
            avg_latency_ms=round(overview.avg_latency_ms, 3)
            if overview.avg_latency_ms is not None
            else None,
            p50_ms=round(overview.p50_ms, 3) if overview.p50_ms is not None else None,
            p95_ms=round(overview.p95_ms, 3) if overview.p95_ms is not None else None,
            p99_ms=round(overview.p99_ms, 3) if overview.p99_ms is not None else None,
            current_rps=round(overview.current_rps, 4),
            current_window_requests=overview.current_window_requests,
        ),
        buckets=[_bucket_response(bucket) for bucket in metrics.buckets],
        values=[None if value is None else round(value, 4) for value in metrics.series()],
    )


# ---------------------------------------------------------------------------
# API Logs
# ---------------------------------------------------------------------------


@router.get("/api-logs/metrics", response_model=ApiMetricsResponse)
async def api_log_metrics(
    current_user: CurrentUser,
    service: TelemetryService,
    range_key: Annotated[str, Query(alias="range")] = "1h",
    metric: str = "rate",
    bucket_seconds: Annotated[int | None, Query(ge=1, le=3600)] = None,
    api_key_id: str | None = None,
    method: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    route: str | None = None,
    request_id: str | None = None,
    outcome: str | None = None,
) -> ApiMetricsResponse:
    """Time-bucketed aggregates for the chart and the summary cards.

    The filter parameters are the **same** ones the log table takes, and both
    sides apply them through the same predicate builder — so narrowing the table
    narrows the chart identically, which is the only behaviour that is not
    confusing.
    """
    window = _resolve_range(service, range_key)
    filters = _event_filters(api_key_id, method, status_filter, route, request_id, outcome)
    metrics = await service.metrics(
        current_user.id,
        window=window,
        metric=_parse_metric(metric),
        filters=filters,
        bucket_override=bucket_seconds,
    )
    return _metrics_response(window, metrics)


@router.get("/api-logs", response_model=ApiLogListResponse)
async def list_api_logs(
    current_user: CurrentUser,
    service: TelemetryService,
    range_key: Annotated[str, Query(alias="range")] = "1h",
    api_key_id: str | None = None,
    method: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    route: str | None = None,
    request_id: str | None = None,
    outcome: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> ApiLogListResponse:
    """One page of request logs, keyset-paginated and filtered server-side."""
    window = _resolve_range(service, range_key)
    filters = _event_filters(api_key_id, method, status_filter, route, request_id, outcome)
    page = await service.logs(
        current_user.id,
        window=window,
        filters=filters,
        cursor=cursor,
        limit=_page_size(limit),
    )
    return ApiLogListResponse(
        items=[_log_entry(event) for event in page.items],
        next_cursor=page.next_cursor,
        range_key=window.key,
        start=window.start,
        end=window.end,
    )


@router.get("/api-logs/stream")
async def stream_api_metrics(
    request: Request,
    current_user: CurrentUser,
    stream_session_factory: Annotated[Any, Depends(get_stream_session_factory)],
    range_key: Annotated[str, Query(alias="range")] = "1h",
    metric: str = "rate",
    api_key_id: str | None = None,
    method: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    route: str | None = None,
    request_id: str | None = None,
    outcome: str | None = None,
) -> Response:
    """Live aggregate updates over Server-Sent Events.

    ## Authentication

    The bearer token is required — this is the same ``CurrentUser`` dependency as
    every other endpoint, and it is delivered in the ``Authorization`` header.
    Native ``EventSource`` cannot send that header, so the SPA reads this stream
    with ``fetch`` + a body reader, exactly as it already does for job progress
    and assistant chat. **No credential is ever placed in the query string**:
    that would put a token in browser history, in proxy logs and in ``Referer``
    headers.

    ## What is sent

    A freshly computed *aggregate snapshot* per tick, not one event per request.
    Publishing every request to every connected dashboard would make the
    dashboard itself the heaviest load on the API; a snapshot per interval means
    a stream costs one small query per interval regardless of traffic volume.

    ## Correctness across instances

    Each tick re-reads the database through a **fresh session**, so the numbers
    are cluster-wide rather than process-local: a request served by another
    replica appears in this stream. Nothing is held in an in-process list, which
    is what makes that true.

    ## Resource bounds

    A per-process semaphore caps concurrent streams; beyond it the endpoint
    answers ``503`` immediately rather than accepting a connection it cannot
    afford to poll. Each connection holds no database session between ticks, is
    cancellation-aware, and stops as soon as the client disconnects.
    """
    window = _range_from_settings(range_key)
    metric_value = _parse_metric(metric)
    filters = _event_filters(api_key_id, method, status_filter, route, request_id, outcome)
    settings = get_settings()
    interval = max(1.0, settings.TELEMETRY_SSE_INTERVAL_SECONDS)
    limit = settings.TELEMETRY_SSE_MAX_CONNECTIONS

    if not _try_acquire_stream(limit):
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content="Too many live dashboard connections.",
            media_type="text/plain",
        )

    account_id = current_user.id
    range_key_value = window.key

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # `retry` tells a reconnecting client how long to wait, which keeps
            # a dropped tab from reconnecting in a tight loop.
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = await _snapshot_payload(
                        account_id,
                        range_key_value,
                        metric_value,
                        filters,
                        settings,
                        stream_session_factory,
                    )
                except Exception as exc:  # noqa: BLE001 — a bad tick must not kill the stream
                    logger.warning("Live metrics tick failed: %s", exc)
                    yield _sse("error", json.dumps({"message": "Metrics temporarily unavailable."}))
                    await asyncio.sleep(interval)
                    continue

                yield _sse("metrics", payload)
                # A comment line, so the client's reader sees activity (and any
                # intermediary keeps the connection open) even when nothing
                # changed.
                yield ": tick\n\n"
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    return
        finally:
            # Released exactly once, on every exit path (client disconnect,
            # cancellation, or an exception), so a dropped tab cannot leak a
            # slot and eventually lock everyone out of Live mode.
            _release_stream()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx (and Render's proxy) not to buffer the stream — without
            # it the frames arrive in bursts or not at all.
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


#: Per-process count of open Live streams. Checked and incremented with no
#: ``await`` in between, so on a single-threaded event loop the check-and-set is
#: atomic and a simple counter is sufficient (a semaphore would only be needed
#: if the limit had to be awaited).
_STREAM_CONNECTIONS = 0


def _try_acquire_stream(limit: int) -> bool:
    global _STREAM_CONNECTIONS
    if _STREAM_CONNECTIONS >= max(1, limit):
        return False
    _STREAM_CONNECTIONS += 1
    return True


def _release_stream() -> None:
    global _STREAM_CONNECTIONS
    _STREAM_CONNECTIONS = max(0, _STREAM_CONNECTIONS - 1)


async def _snapshot_payload(
    account_id: int,
    range_key: str,
    metric: ApiMetric,
    filters: ApiLogFilters,
    settings,
    stream_session_factory,
) -> str:
    """Compute one live snapshot on a short-lived session.

    A fresh session per tick (rather than the request's session) is deliberate:
    a stream can live for hours, and holding one pooled connection for that long
    would starve the pool for real requests.
    """
    async with stream_session_factory() as session:
        service = ApiTelemetryService(
            SQLTelemetryRepository(session),
            max_range_days=settings.TELEMETRY_MAX_RANGE_DAYS,
            current_window_seconds=settings.TELEMETRY_CURRENT_WINDOW_SECONDS,
        )
        window = service.resolve_range(range_key)
        metrics = await service.metrics(
            account_id, window=window, metric=metric, filters=filters
        )
        return _metrics_response(window, metrics).model_dump_json()


@router.get("/api-logs/{event_id}", response_model=ApiLogDetailResponse)
async def get_api_log(
    event_id: str, current_user: CurrentUser, service: TelemetryService
) -> ApiLogDetailResponse:
    """One request's detail.

    Scoped by account, so an id belonging to another account is reported as
    ``404`` — identical to a nonexistent id, which means the endpoint cannot be
    used to discover whether a given request id exists.
    """
    event = await service.get_log(current_user.id, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return ApiLogDetailResponse(
        entry=_log_entry(event),
        status_meaning=_status_meaning(event.status_code),
        via_session=event.api_key_id is None,
    )


# ---------------------------------------------------------------------------
# MCP Activity
# ---------------------------------------------------------------------------


def _connection_response(view: McpConnectionView) -> McpConnectionResponse:
    granted = set(view.scopes)
    return McpConnectionResponse(
        id=view.id,
        client_id=view.client_id,
        client_name=view.client_name,
        status=view.status.value,
        scopes=[scope.value for scope in view.scopes],
        permissions=[
            McpPermissionResponse(
                scope=scope.value,
                description=SCOPE_DESCRIPTIONS[scope],
                granted=scope in granted,
                destructive=scope in DESTRUCTIVE_SCOPES,
            )
            for scope in AgentScope
        ],
        created_at=view.created_at,
        last_seen_at=view.last_seen_at,
        last_activity_at=view.last_activity_at,
        paused_at=view.paused_at,
        revoked_at=view.revoked_at,
        requests=view.requests,
        errors=view.errors,
        denied=view.denied,
    )


def _activity_entry(item) -> McpActivityEntry:
    return McpActivityEntry(
        id=item.id,
        timestamp=item.created_at,
        connection_id=item.grant_id,
        client_id=item.client_id,
        client_name=item.client_name,
        tool_name=item.tool_name,
        outcome=item.outcome,
        error_category=item.error_category,
        duration_ms=item.duration_ms,
        request_id=item.request_id,
    )


@router.get("/mcp/connections", response_model=McpConnectionListResponse)
async def list_mcp_connections(
    current_user: CurrentUser,
    service: McpActivity,
    range_key: Annotated[str, Query(alias="range")] = "24h",
) -> McpConnectionListResponse:
    """The account's authorized AI-agent connections, with in-window activity."""
    window = _range_from_settings(range_key)
    connections = await service.connections(current_user.id, window=window)
    return McpConnectionListResponse(
        connections=[_connection_response(view) for view in connections],
        range_key=window.key,
        start=window.start,
        end=window.end,
    )


@router.get("/mcp/summary", response_model=McpActivitySummaryResponse)
async def mcp_activity_summary(
    current_user: CurrentUser,
    service: McpActivity,
    range_key: Annotated[str, Query(alias="range")] = "24h",
) -> McpActivitySummaryResponse:
    """The three header numbers plus the tool list, for one window."""
    window = _range_from_settings(range_key)
    summary = await service.summary(current_user.id, window=window)
    return McpActivitySummaryResponse(
        connections=summary.connections,
        active_connections=summary.active_connections,
        paused_connections=summary.paused_connections,
        revoked_connections=summary.revoked_connections,
        requests=summary.requests,
        errors=summary.errors,
        denied=summary.denied,
        tools_used=summary.tools_used,
        range_key=window.key,
        start=window.start,
        end=window.end,
    )


@router.get("/mcp/activity", response_model=McpActivityListResponse)
async def list_mcp_activity(
    current_user: CurrentUser,
    service: McpActivity,
    range_key: Annotated[str, Query(alias="range")] = "24h",
    connection_id: str | None = None,
    tool_name: str | None = None,
    outcome: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
) -> McpActivityListResponse:
    """The MCP tool-call log, filtered and keyset-paginated server-side."""
    window = _range_from_settings(range_key)
    if outcome is not None and outcome not in ("SUCCESS", "ERROR", "DENIED"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_OUTCOME",
                "message": "outcome must be SUCCESS, ERROR or DENIED.",
            },
        )
    page = await service.activity(
        current_user.id,
        window=window,
        filters=McpActivityFilters(
            grant_id=connection_id, tool_name=tool_name, outcome=outcome
        ),
        cursor=cursor,
        limit=_page_size(limit),
    )
    return McpActivityListResponse(
        items=[_activity_entry(item) for item in page.items],
        next_cursor=page.next_cursor,
        range_key=window.key,
        start=window.start,
        end=window.end,
    )


async def _connection_after(
    activity: McpActivityService, account_id: int, grant_id: str
) -> McpConnectionResponse | None:
    """Re-read one connection so a mutation answers with the stored truth."""
    now = datetime.now(UTC)
    window = ResolvedRange(start=now - timedelta(days=1), end=now, key="24h")
    connections = await activity.connections(account_id, window=window)
    for view in connections:
        if view.id == grant_id:
            return _connection_response(view)
    return None


async def _set_connection_status(
    action: str,
    connection_id: str,
    current_user: CurrentUser,
    access: MCPAccessService,
    activity: McpActivityService,
) -> McpControlResponse:
    account_id = current_user.id
    try:
        if action == "pause":
            grant = await access.pause_connection(account_id, connection_id)
        elif action == "resume":
            grant = await access.resume_connection(account_id, connection_id)
        else:
            grant = await access.revoke_connection(account_id, connection_id)
    except MCPAccessError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.description) from exc

    if grant is None:
        # Not found and not-owned are the same answer, on purpose: a distinct
        # 403 would confirm that somebody else's grant id exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    connection = await _connection_after(activity, account_id, grant.id)
    if connection is None:  # pragma: no cover - the grant was just written
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Connection state could not be reloaded.",
        )
    messages = {
        "pause": "Connection paused. The agent cannot make further requests until you resume it.",
        "resume": "Connection resumed with its original permissions.",
        "revoke": "Connection revoked. The agent must be authorized again to regain access.",
    }
    return McpControlResponse(connection=connection, message=messages[action])


@router.post("/mcp/connections/{connection_id}/pause", response_model=McpControlResponse)
async def pause_mcp_connection(
    connection_id: str,
    current_user: CurrentUser,
    access: McpAccess,
    activity: McpActivity,
) -> McpControlResponse:
    """Suspend a connection. Effective on the agent's next request."""
    return await _set_connection_status("pause", connection_id, current_user, access, activity)


@router.post("/mcp/connections/{connection_id}/resume", response_model=McpControlResponse)
async def resume_mcp_connection(
    connection_id: str,
    current_user: CurrentUser,
    access: McpAccess,
    activity: McpActivity,
) -> McpControlResponse:
    """Undo a pause, restoring exactly the previously granted permissions."""
    return await _set_connection_status("resume", connection_id, current_user, access, activity)


@router.post("/mcp/connections/{connection_id}/revoke", response_model=McpControlResponse)
async def revoke_mcp_connection(
    connection_id: str,
    current_user: CurrentUser,
    access: McpAccess,
    activity: McpActivity,
) -> McpControlResponse:
    """Withdraw consent and destroy the connection's credentials.

    Distinct from pausing: the access and refresh tokens for this grant are
    revoked, so nothing issued earlier can be revived by flipping the state
    back. Regaining access requires a fresh OAuth consent.
    """
    return await _set_connection_status("revoke", connection_id, current_user, access, activity)
