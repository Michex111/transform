"""Request/response schemas for the Developer observability API.

These describe exactly what the two Developer pages render. Two rules shaped
them:

* **No field exists that the backend does not actually record.** There is no
  ``response_body``, no ``headers``, no ``span_breakdown``. A schema field with
  nothing behind it is an invitation to fabricate a value, so the shape itself
  enforces honesty.
* **Derived values are named as derived.** ``status_meaning`` is computed from
  the status code and says so; it is not a recorded response summary that
  happened to go missing. Where the backend has no measurement (sub-stage
  timings, byte counts the framework did not report), the field is optional and
  the UI omits it rather than printing a placeholder.
"""

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# API Logs — metrics
# ---------------------------------------------------------------------------


class MetricsBucketResponse(BaseModel):
    """One time bucket. Every series is present so the chart can switch metric
    without a refetch; ``null`` means "no samples", not zero."""

    t: datetime = Field(description="Bucket start, pinned to the epoch grid.")
    count: int
    success: int
    errors: int
    rate: float = Field(description="Average requests per second within the bucket.")
    error_rate: float = Field(description="Percentage of requests in the bucket that errored.")
    avg_latency_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None


class MetricsOverviewResponse(BaseModel):
    """Window totals plus the current-rate reading."""

    requests: int
    success: int
    errors: int
    success_rate: float
    error_rate: float
    avg_latency_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    current_rps: float
    current_window_requests: int = Field(
        description="Requests in the current-rate window, so the decimal is explainable."
    )


class ApiMetricsResponse(BaseModel):
    """The complete chart payload: metadata, totals and the filled bucket list."""

    range_key: str
    start: datetime
    end: datetime
    bucket_seconds: int = Field(description="Size of each bucket; the chart's x-axis interval.")
    metric: str = Field(description="The selected series (rate | count | p50 | p95 | error_rate).")
    metric_label: str
    metric_unit: str
    generated_at: datetime = Field(description="When these aggregates were computed.")
    overview: MetricsOverviewResponse
    buckets: list[MetricsBucketResponse]
    values: list[float | None] = Field(
        description="The selected metric's value per bucket, in bucket order. A null is a real gap."
    )


# ---------------------------------------------------------------------------
# API Logs — the request explorer
# ---------------------------------------------------------------------------


class ApiLogEntry(BaseModel):
    """One request, as the log table and the detail drawer show it.

    ``api_key_name`` is a display label resolved at read time; ``api_key_id`` is
    the stable identifier. Neither is the secret.
    """

    id: str
    timestamp: datetime
    method: str
    route: str = Field(description="Matched route template, e.g. /api/v1/files/{file_id}.")
    status_code: int
    outcome: str = Field(description="success | error, derived from the status code.")
    duration_ms: float
    request_id: str
    api_key_id: str | None = None
    #: ``None`` for a session-authenticated (SPA) request, which uses no API key.
    api_key_name: str | None = None
    environment: str | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None


class ApiLogListResponse(BaseModel):
    items: list[ApiLogEntry]
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page; null on the last page."
    )
    range_key: str
    start: datetime
    end: datetime


class ApiLogDetailResponse(BaseModel):
    """The detail drawer's payload.

    ``status_meaning`` is **derived from the status code** — the backend records
    no response body, so this is a description of the status, clearly one step
    removed from the request itself.
    """

    entry: ApiLogEntry
    status_meaning: str
    #: True when the request was made with the account's own session (the SPA),
    #: rather than an API key. The UI says "Dashboard session" for these.
    via_session: bool


# ---------------------------------------------------------------------------
# MCP Activity
# ---------------------------------------------------------------------------


class McpPermissionResponse(BaseModel):
    scope: str
    description: str
    granted: bool
    destructive: bool = False


class McpConnectionResponse(BaseModel):
    """One authorized connection (a user's grant to one agent application)."""

    id: str
    client_id: str
    #: Untrusted display name supplied by the application; render as text.
    client_name: str
    status: str = Field(description="ACTIVE | PAUSED | REVOKED — authorization state.")
    scopes: list[str]
    permissions: list[McpPermissionResponse]
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_activity_at: datetime | None = None
    paused_at: datetime | None = None
    revoked_at: datetime | None = None
    requests: int = Field(description="Tool calls in the selected window.")
    errors: int
    denied: int


class McpConnectionListResponse(BaseModel):
    connections: list[McpConnectionResponse]
    range_key: str
    start: datetime
    end: datetime


class McpActivityEntry(BaseModel):
    """One MCP tool invocation."""

    id: str
    timestamp: datetime
    connection_id: str
    client_id: str
    #: Untrusted display name resolved from the grant at read time.
    client_name: str | None = None
    tool_name: str
    outcome: str = Field(description="SUCCESS | ERROR | DENIED.")
    error_category: str | None = None
    duration_ms: float | None = None
    request_id: str | None = None


class McpActivityListResponse(BaseModel):
    items: list[McpActivityEntry]
    next_cursor: str | None = None
    range_key: str
    start: datetime
    end: datetime


class McpActivitySummaryResponse(BaseModel):
    connections: int
    active_connections: int
    paused_connections: int
    revoked_connections: int
    requests: int
    errors: int
    denied: int
    tools_used: list[str]
    range_key: str
    start: datetime
    end: datetime


class McpControlResponse(BaseModel):
    """The connection after a pause/resume/revoke, from the authoritative store.

    Returned by every mutation so the UI updates from the server's answer rather
    than from an optimistic guess — a control that only *looks* applied is worse
    than none.
    """

    connection: McpConnectionResponse
    message: str
