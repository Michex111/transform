/**
 * Types for the Developer observability API (API Logs + MCP Activity).
 *
 * Kept in their own module rather than growing `types.ts` further: this is a
 * self-contained product surface with its own vocabulary, and the two pages
 * import from here so a change to a metric field is visible in one diff.
 *
 * Two shapes of trust are represented, and the comments say which is which:
 *
 *  - `metric_*` / `overview` / `buckets` are **server-computed**. The client
 *    never derives a rate, a percentile or a success rate — if it did, the
 *    chart and the summary cards could disagree.
 *  - `client_name` on an MCP connection is **untrusted text** supplied by a
 *    third-party application. Render it as text.
 */

/** A metric the chart can plot. Mirrors the server's `ApiMetric` enum. */
export type ApiMetric = "rate" | "count" | "p50" | "p95" | "error_rate"

/** A selectable window. Mirrors the server's `RANGE_PRESETS`. */
export type ApiLogRange = "5m" | "15m" | "1h" | "24h" | "7d" | "30d"

export interface ApiMetricsBucket {
  /** Bucket start, ISO-8601 UTC. */
  t: string
  count: number
  success: number
  errors: number
  /** Average requests per second **within** this bucket. */
  rate: number
  /** Percentage of this bucket's requests that errored (0–100). */
  error_rate: number
  avg_latency_ms: number | null
  p50_ms: number | null
  p95_ms: number | null
  p99_ms: number | null
}

export interface ApiMetricsOverview {
  requests: number
  success: number
  errors: number
  success_rate: number
  error_rate: number
  avg_latency_ms: number | null
  p50_ms: number | null
  p95_ms: number | null
  p99_ms: number | null
  /** Requests per second over the server's current-rate window. */
  current_rps: number
  current_window_requests: number
}

export interface ApiMetricsResponse {
  range_key: string
  start: string
  end: string
  bucket_seconds: number
  metric: string
  metric_label: string
  metric_unit: string
  /** When the server computed these aggregates — the freshness timestamp. */
  generated_at: string
  overview: ApiMetricsOverview
  buckets: ApiMetricsBucket[]
  /** The selected metric's value per bucket; `null` is a real gap, not zero. */
  values: Array<number | null>
}

export interface ApiLogEntry {
  id: string
  timestamp: string
  method: string
  /** The matched route template, e.g. `/api/v1/files/{file_id}`. */
  route: string
  status_code: number
  outcome: "success" | "error"
  duration_ms: number
  request_id: string
  api_key_id: string | null
  /** `null` for a session (SPA) request, which uses no API key. */
  api_key_name: string | null
  environment: string | null
  request_bytes: number | null
  response_bytes: number | null
}

export interface ApiLogListResponse {
  items: ApiLogEntry[]
  /** Opaque keyset cursor; `null` on the last page. */
  next_cursor: string | null
  range_key: string
  start: string
  end: string
}

export interface ApiLogDetailResponse {
  entry: ApiLogEntry
  /** Derived from the status code by the server — not a recorded body. */
  status_meaning: string
  via_session: boolean
}

/** Filters shared by the metric query and the log table. */
export interface ApiLogFilters {
  range: ApiLogRange
  apiKeyId?: string
  method?: string
  /** Exact code (`500`) or class (`5xx`). */
  status?: string
  route?: string
  requestId?: string
  outcome?: "success" | "error"
  cursor?: string
  limit?: number
}

export type McpConnectionStatus = "ACTIVE" | "PAUSED" | "REVOKED"

export interface McpPermission {
  scope: string
  description: string
  granted: boolean
  destructive: boolean
}

export interface McpConnection {
  id: string
  client_id: string
  /** Untrusted: supplied by the third-party application at registration. */
  client_name: string
  /** Authorization state — not a claim of live session connectivity. */
  status: McpConnectionStatus
  scopes: string[]
  permissions: McpPermission[]
  created_at: string | null
  last_seen_at: string | null
  last_activity_at: string | null
  paused_at: string | null
  revoked_at: string | null
  /** Tool calls in the selected window. */
  requests: number
  errors: number
  denied: number
}

export interface McpConnectionListResponse {
  connections: McpConnection[]
  range_key: string
  start: string
  end: string
}

export interface McpActivityEntry {
  id: string
  timestamp: string
  connection_id: string
  client_id: string
  client_name: string | null
  tool_name: string
  outcome: "SUCCESS" | "ERROR" | "DENIED"
  error_category: string | null
  duration_ms: number | null
  request_id: string | null
}

export interface McpActivityListResponse {
  items: McpActivityEntry[]
  next_cursor: string | null
  range_key: string
  start: string
  end: string
}

export interface McpActivitySummaryResponse {
  connections: number
  active_connections: number
  paused_connections: number
  revoked_connections: number
  requests: number
  errors: number
  denied: number
  tools_used: string[]
  range_key: string
  start: string
  end: string
}

export interface McpActivityFilters {
  range: ApiLogRange
  connectionId?: string
  toolName?: string
  outcome?: "SUCCESS" | "ERROR" | "DENIED"
  cursor?: string
  limit?: number
}

/** The authoritative connection after a pause/resume/revoke. */
export interface McpControlResponse {
  connection: McpConnection
  message: string
}
