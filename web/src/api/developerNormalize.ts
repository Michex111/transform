/**
 * Runtime shape guards for the Developer observability responses.
 *
 * Same reasoning as `normalize.ts`, and the same boundary: these run at the
 * client's edge so the declared types are true by construction. That matters
 * more than usual here because both pages render *arrays of rows* and *numeric
 * aggregates* — a missing `buckets` would make the chart throw on `.map`, and a
 * missing `overview` would render `NaN%` in a summary card, which reads as a
 * real measurement rather than as a broken response.
 *
 * Every normaliser is lossless for a well-formed payload.
 */

import type {
  ApiLogDetailResponse,
  ApiLogEntry,
  ApiLogListResponse,
  ApiMetricsBucket,
  ApiMetricsOverview,
  ApiMetricsResponse,
  McpActivityEntry,
  McpActivityListResponse,
  McpActivitySummaryResponse,
  McpConnection,
  McpConnectionListResponse,
  McpControlResponse,
  McpPermission,
} from "./developerTypes";

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/**
 * A finite number, or `null`.
 *
 * `null` (rather than 0) for a missing value is deliberate and load-bearing:
 * the chart breaks its line at a `null` and the UI omits the field. Coercing to
 * 0 would draw a latency spike to the floor and print "0 B" for a size the
 * server never measured.
 */
function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

// ---------------------------------------------------------------------------
// API Logs — metrics
// ---------------------------------------------------------------------------

function normalizeBucket(raw: unknown): ApiMetricsBucket {
  const bucket = (raw ?? {}) as Record<string, unknown>;
  return {
    t: asString(bucket.t),
    count: asNumber(bucket.count),
    success: asNumber(bucket.success),
    errors: asNumber(bucket.errors),
    rate: asNumber(bucket.rate),
    error_rate: asNumber(bucket.error_rate),
    avg_latency_ms: asNullableNumber(bucket.avg_latency_ms),
    p50_ms: asNullableNumber(bucket.p50_ms),
    p95_ms: asNullableNumber(bucket.p95_ms),
    p99_ms: asNullableNumber(bucket.p99_ms),
  };
}

function normalizeOverview(raw: unknown): ApiMetricsOverview {
  const overview = (raw ?? {}) as Record<string, unknown>;
  return {
    requests: asNumber(overview.requests),
    success: asNumber(overview.success),
    errors: asNumber(overview.errors),
    success_rate: asNumber(overview.success_rate),
    error_rate: asNumber(overview.error_rate),
    avg_latency_ms: asNullableNumber(overview.avg_latency_ms),
    p50_ms: asNullableNumber(overview.p50_ms),
    p95_ms: asNullableNumber(overview.p95_ms),
    p99_ms: asNullableNumber(overview.p99_ms),
    current_rps: asNumber(overview.current_rps),
    current_window_requests: asNumber(overview.current_window_requests),
  };
}

export function normalizeApiMetrics(raw: unknown): ApiMetricsResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  const buckets = asArray(body.buckets).map(normalizeBucket);
  // The server sends `values`, but deriving them from the buckets when absent
  // keeps the chart renderable against an older API rather than blank.
  const values = asArray(body.values).map(asNullableNumber);
  return {
    range_key: asString(body.range_key, "1h"),
    start: asString(body.start),
    end: asString(body.end),
    bucket_seconds: asNumber(body.bucket_seconds, 60),
    metric: asString(body.metric, "rate"),
    metric_label: asString(body.metric_label, "Request rate"),
    metric_unit: asString(body.metric_unit, "req/s"),
    generated_at: asString(body.generated_at),
    overview: normalizeOverview(body.overview),
    buckets,
    values: values.length === buckets.length ? values : buckets.map(() => null),
  };
}

// ---------------------------------------------------------------------------
// API Logs — explorer
// ---------------------------------------------------------------------------

function normalizeEntry(raw: unknown): ApiLogEntry {
  const entry = (raw ?? {}) as Record<string, unknown>;
  const status = asNumber(entry.status_code);
  return {
    id: asString(entry.id),
    timestamp: asString(entry.timestamp),
    method: asString(entry.method, "GET"),
    route: asString(entry.route, "unmatched"),
    status_code: status,
    outcome: entry.outcome === "error" || status >= 400 ? "error" : "success",
    duration_ms: asNumber(entry.duration_ms),
    request_id: asString(entry.request_id),
    api_key_id: asNullableString(entry.api_key_id),
    api_key_name: asNullableString(entry.api_key_name),
    environment: asNullableString(entry.environment),
    request_bytes: asNullableNumber(entry.request_bytes),
    response_bytes: asNullableNumber(entry.response_bytes),
  };
}

export function normalizeApiLogList(raw: unknown): ApiLogListResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    items: asArray(body.items).map(normalizeEntry),
    next_cursor: asNullableString(body.next_cursor),
    range_key: asString(body.range_key, "1h"),
    start: asString(body.start),
    end: asString(body.end),
  };
}

export function normalizeApiLogDetail(raw: unknown): ApiLogDetailResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  const entry = normalizeEntry(body.entry);
  return {
    entry,
    status_meaning: asString(body.status_meaning, "The request completed."),
    via_session: body.via_session === true || entry.api_key_id === null,
  };
}

// ---------------------------------------------------------------------------
// MCP Activity
// ---------------------------------------------------------------------------

function normalizePermission(raw: unknown): McpPermission {
  const permission = (raw ?? {}) as Record<string, unknown>;
  return {
    scope: asString(permission.scope),
    description: asString(permission.description),
    granted: permission.granted === true,
    destructive: permission.destructive === true,
  };
}

const CONNECTION_STATUSES = ["ACTIVE", "PAUSED", "REVOKED"] as const;

function normalizeConnection(raw: unknown): McpConnection {
  const connection = (raw ?? {}) as Record<string, unknown>;
  const status = asString(connection.status, "ACTIVE");
  return {
    id: asString(connection.id),
    client_id: asString(connection.client_id),
    // Untrusted text; kept as a plain string and rendered as text.
    client_name: asString(connection.client_name, "Unnamed application"),
    status: (CONNECTION_STATUSES as readonly string[]).includes(status)
      ? (status as McpConnection["status"])
      : // An unrecognised status must fail closed in the UI. `REVOKED` is the
        // safe reading: it hides the "active" affordances rather than offering
        // a Resume for a connection whose state we do not understand.
        "REVOKED",
    scopes: asArray(connection.scopes).filter((s): s is string => typeof s === "string"),
    permissions: asArray(connection.permissions).map(normalizePermission),
    created_at: asNullableString(connection.created_at),
    last_seen_at: asNullableString(connection.last_seen_at),
    last_activity_at: asNullableString(connection.last_activity_at),
    paused_at: asNullableString(connection.paused_at),
    revoked_at: asNullableString(connection.revoked_at),
    requests: asNumber(connection.requests),
    errors: asNumber(connection.errors),
    denied: asNumber(connection.denied),
  };
}

export function normalizeMcpConnections(raw: unknown): McpConnectionListResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    connections: asArray(body.connections).map(normalizeConnection),
    range_key: asString(body.range_key, "24h"),
    start: asString(body.start),
    end: asString(body.end),
  };
}

const OUTCOMES = ["SUCCESS", "ERROR", "DENIED"] as const;

function normalizeActivity(raw: unknown): McpActivityEntry {
  const item = (raw ?? {}) as Record<string, unknown>;
  const outcome = asString(item.outcome, "ERROR");
  return {
    id: asString(item.id),
    timestamp: asString(item.timestamp),
    connection_id: asString(item.connection_id),
    client_id: asString(item.client_id),
    client_name: asNullableString(item.client_name),
    tool_name: asString(item.tool_name, "unknown"),
    outcome: (OUTCOMES as readonly string[]).includes(outcome)
      ? (outcome as McpActivityEntry["outcome"])
      : "ERROR",
    error_category: asNullableString(item.error_category),
    duration_ms: asNullableNumber(item.duration_ms),
    request_id: asNullableString(item.request_id),
  };
}

export function normalizeMcpActivityList(raw: unknown): McpActivityListResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    items: asArray(body.items).map(normalizeActivity),
    next_cursor: asNullableString(body.next_cursor),
    range_key: asString(body.range_key, "24h"),
    start: asString(body.start),
    end: asString(body.end),
  };
}

export function normalizeMcpSummary(raw: unknown): McpActivitySummaryResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    connections: asNumber(body.connections),
    active_connections: asNumber(body.active_connections),
    paused_connections: asNumber(body.paused_connections),
    revoked_connections: asNumber(body.revoked_connections),
    requests: asNumber(body.requests),
    errors: asNumber(body.errors),
    denied: asNumber(body.denied),
    tools_used: asArray(body.tools_used).filter((t): t is string => typeof t === "string"),
    range_key: asString(body.range_key, "24h"),
    start: asString(body.start),
    end: asString(body.end),
  };
}

export function normalizeMcpControl(raw: unknown): McpControlResponse {
  const body = (raw ?? {}) as Record<string, unknown>;
  return {
    connection: normalizeConnection(body.connection),
    message: asString(body.message, "Connection updated."),
  };
}

/** Exposed for tests: the single-connection guard used by the list and control paths. */
export { normalizeConnection };
