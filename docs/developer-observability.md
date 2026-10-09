# Developer observability: API Logs and MCP Activity

The Developer section of the app has two pages:

| Page | Route | Answers |
|---|---|---|
| **API Logs** | `/app/developer/api-logs` | What are my API keys doing? How fast, how often, and how often does it fail? |
| **MCP Activity** | `/app/developer/mcp-activity` | Which AI applications are connected, what have they done, and can I stop one? |

They are deliberately separate surfaces. An API request is identified by a
*credential* and a *route*; an MCP tool call is identified by an *authorization
grant* and a *tool*. Merging them would force every reader to remember which
half of the columns applied.

---

## 1. What is captured

### API requests

One row per **attributable** public API request.

| Field | Notes |
|---|---|
| `account_id` | The owning account. Every read is scoped by it. |
| `api_key_id` | Which key authenticated the call. `null` for the SPA's session JWT. |
| `request_id` | Correlation id, echoed from `X-Request-ID` when safe, otherwise generated. |
| `created_at`, `method`, `route_template`, `status_code`, `duration_ms` | The request itself. |
| `environment`, `request_bytes`, `response_bytes` | Best-effort. `null` means "not measured", never `0`. |

**Attribution comes from verified authentication state**, not from a request
header. The authentication dependency writes the account and key id onto the
request after it has verified the credential; the capture middleware reads that
after the response is produced. A caller cannot influence it.

**Requests that never authenticated are not recorded.** A 401, a 429 from the
rate limiter, or a request that matched no route has no owner to attribute to,
and inventing one would put another user's traffic in someone's dashboard.
Those remain visible in the platform's own Prometheus metrics.

### MCP tool invocations

One row per tool call made by an authorized agent.

`account_id`, `grant_id`, `client_id`, `tool_name`, `outcome`
(`SUCCESS` / `ERROR` / `DENIED`), `error_category`, `duration_ms`,
`request_id`, `created_at`. The display name is resolved from the grant at read
time, so it always reflects the current label.

### What is never captured

No request or response **bodies**, no **headers**, no **cookies**, no
**Authorization** values, no **signed storage URLs**, no **file contents**, no
**tool arguments**, and never an **API key or OAuth token**. The schemas have no
column for any of them, which is a stronger guarantee than a policy of not
logging them: a capture layer cannot persist a field that does not exist.

Routes are stored as **templates** (`/api/v1/files/{file_id}`), never as raw
paths, so ids never enter the table and "requests by endpoint" stays a finite
grouping rather than one series per file.

---

## 2. Exclusions

Capture skips, by design:

| Path | Why |
|---|---|
| `/health`, `/ready`, `/metrics` | Infrastructure probes; they would swamp real history. |
| `/docs`, `/redoc`, `/openapi.json` | Static documentation. |
| `/.well-known`, `/mcp` | The MCP transport. Those calls are captured, richer, as **tool invocations**; logging both would double-count. |
| `/api/v1/developer/*` | The dashboard's own reads. Measuring the instrument would make every refresh add rows. |
| `/api/v1/events/*`, `*/stream` | Long-lived streams. A ten-minute connection's "duration" is its lifetime, not the server's work, and would poison the latency percentiles. |

---

## 3. Metrics, definitions and units

The chart can plot five series. Every one is computed **server-side** from
stored rows; the browser never derives a rate or a percentile, so the chart and
the summary cards cannot disagree.

| Metric | Unit | Definition |
|---|---|---|
| Request rate | `req/s` | `count / bucket_seconds` — the **average within each bucket**, not an instantaneous reading. The interval size is shown beside the chart. |
| Requests | `requests` | Requests that completed within the bucket. |
| p50 / p95 latency | `ms` | Percentiles of request duration, linear-interpolated. |
| Error rate | `%` | `errors / count × 100` for the bucket. |

### Outcome semantics

- **Success** = HTTP status **< 400**. 1xx, 2xx and 3xx all count. A 3xx is a
  normal answer for this API (`/mcp` → `/mcp/`); counting it as a failure would
  make MCP enabled accounts look broken.
- **Error** = status **≥ 400**.
- There is no third bucket. A request whose client disconnected before a
  response existed is not recorded at all — there is no honest status to
  attribute.

### Bucket sizing

Buckets are chosen from a fixed ladder `(1, 5, 10, 15, 30, 60, 300, 900, 1800,
3600)` seconds: the smallest size that yields no more than ~120 points for the
selected range. The 7-day preset therefore lands on hourly buckets (168 points).

Bucket starts are **aligned to the epoch** (`floor(epoch / size) × size`), not to
the query window. So the 10:00:00 bucket covers 10:00:00–10:00:10 whether the
window begins at 09:57 or at 10:00, and a refresh does not shift the whole
series sideways.

**Empty buckets are emitted explicitly with `count = 0`.** A gap in the data
renders as a gap in the line, not as a straight line bridging two distant
points — which would read as traffic that never happened. For a latency series,
a bucket with no samples is `null`, not `0`: the line breaks rather than
dropping to zero milliseconds.

### Percentiles

PostgreSQL computes them with `percentile_cont` in the aggregation query.
SQLite (the test dialect) has no such function, so the repository fetches the
window's durations and applies the same interpolation
(`domain/telemetry/value_object/request_metrics.percentile`). Both paths are
pinned to the same reference implementation by unit tests.

### The window overview

Counts are summed from the already-loaded chart buckets, so the header always
agrees with the graph. The window-level **percentiles** come from a second
aggregation that puts the whole range in a single bucket — an average of
per-bucket p95s is not a percentile of anything.

`current_rps` is measured over a fixed 60-second window, independent of the
chart's range: a "current rate" that changed meaning when you changed the zoom
level would not be a current rate.

---

## 4. Ingestion and the write path

Request events are **not** written synchronously on the request path.

```
middleware ──put_nowait──▶ bounded asyncio.Queue ──batch──▶ background flusher ──▶ Postgres
```

Properties, all deliberate:

- **Recording never blocks and never raises.** A full queue drops the item and
  increments a counter rather than awaiting space. Losing telemetry is strictly
  better than slowing down a user's conversion.
- **Dropping is observable.** `/metrics` exposes `telemetry_ingest_queue_depth`,
  `telemetry_ingest_dropped_total`, `telemetry_ingest_write_failures_total` and
  `telemetry_ingest_written_total`, so "we are silently discarding 40% of
  events" is alertable rather than a surprise found during an incident.
- **Failure policy is explicit.** A failed batch is retried **once**; a second
  failure drops it and counts it. Retrying forever against a broken database
  would turn a bounded drop into an unbounded memory leak.
- **Backpressure is the drop.** Producers are never slowed.
- **Shutdown drains.** The lifespan stops the flusher after flushing, so a clean
  deploy does not lose the last seconds of activity.
- **Telemetry can never fail a request.** Everything the capture middleware does
  after the response is produced is wrapped; a fault degrades to "this request
  was not logged".

No new broker or external observability stack was introduced: an in-process
bounded queue plus the existing database already meet the requirement, and the
existing Redis streams are job queues rather than a telemetry pipeline.

---

## 5. Static and live modes

**Static** (default). A snapshot is fetched on load and on an explicit Refresh.
No polling and no stream: a dashboard that quietly polls is exactly what users
switch Live off to avoid. The freshness stamp reads "Updated 10:42:08".

**Live.** An authenticated SSE stream sends a freshly computed **aggregate
snapshot** every `TELEMETRY_SSE_INTERVAL_SECONDS` — never one event per request.
Publishing every request to every connected dashboard would make the dashboard
the heaviest load on the API. Connection state is shown as Connecting / Live /
Reconnecting / Disconnected, with bounded exponential-backoff retry (5 attempts)
before it settles on Disconnected.

### Why not `EventSource`

Native `EventSource` cannot send an `Authorization` header, and the only
alternative it allows is a credential in the query string — which leaks a token
into browser history, proxy logs and `Referer` headers. The stream is therefore
read with `fetch` + a body reader, exactly as the job-progress and assistant
streams already are, and **the bearer token travels in the `Authorization`
header**.

### Correctness across instances

Each tick re-reads the database through a **fresh session**, so the numbers are
cluster-wide rather than process-local: a request served by another replica
appears in the stream. Nothing is held in an in-process event list, so no Redis
pub/sub is required. Each connection holds no pooled connection between ticks,
which is what makes an hours-long stream harmless to the pool.

### Resource limits

A per-process counter caps concurrent streams at
`TELEMETRY_SSE_MAX_CONNECTIONS`; beyond it the endpoint answers **503**
immediately rather than accepting a connection it cannot afford to poll. Every
connection is cancellation-aware, is closed when the client disconnects, and
releases its slot on every exit path.

### Proxy configuration

The response sets `Cache-Control: no-cache, no-transform`,
`Connection: keep-alive` and `X-Accel-Buffering: no`. The last one is what stops
nginx (and Render's proxy) from buffering the stream into bursts; without it
frames arrive late or not at all. No proxy-level timeout change is required: a
`: tick` comment is written on every interval, so the connection is never idle
long enough to be reaped.

---

## 6. API key attribution

- The key id comes from **verified authentication**, never from a header the
  caller controls.
- The dashboard shows the key's **display name** (joined at read time) and its
  non-secret id. The key **secret is never selected** by any query.
- A request authenticated with the SPA's session JWT has no key and is shown as
  **"Dashboard session"** rather than being hidden or given a fabricated name.

---

## 7. MCP identity, and pause vs revoke

**Identity is the verified OAuth grant.** The connection model is built on the
`mcp_agent_grants` row — a specific user's consent for a specific registered
OAuth client — plus the `client_id` from the validated access token. A name the
agent reports about itself is treated as untrusted display text and never as an
identity. **Pausing User A's connection cannot affect User B's**, because every
mutation is scoped by `user_id` in the same statement that loads the row.

The page reports **authorization status**, not live session connectivity. An
`ACTIVE` connection that has not been used for a month is described as active
with a stale last-activity time, never as "connected": a stateless HTTP MCP
server has no session to observe.

| | Pause | Revoke |
|---|---|---|
| Reversible by the user | Yes | No — requires a fresh consent |
| Scopes | Preserved exactly | Preserved (the grant is dead) |
| Credentials | Left in place, but unusable | Access and refresh tokens are revoked |
| Effect | Immediate | Immediate |

**Pause is enforced at the authorization boundary.** Access tokens are opaque and
resolved through the grant row on *every* MCP call; a paused grant is not
`is_active()`, so `load_access_token` returns `None` and the call is refused on
the very next request. There is no window in which a token issued before the
pause still works, and an already-open MCP session cannot bypass it.

**Already-running tool calls are not cancelled.** There is no cooperative
cancellation point in a tool's execution, and claiming otherwise would be a
false promise. The guarantee is that no *subsequent* call succeeds. The
confirmation dialog says exactly this.

**Resume never widens access.** It restores the stored scope set, unmodified, so
a resume after a re-consent that dropped a scope cannot bring it back. A
revoked grant can be neither paused nor resumed (`400`), and an unrecognised
stored status is read as revoked (fail closed).

Every pause/resume/revoke writes a structured audit event
(`mcp_connection_control`) naming the actor, the connection, the action and
whether it succeeded — and never a credential.

---

## 8. API endpoints

All require authentication and scope every query by the caller's account.

```
GET  /api/v1/developer/api-logs/metrics        # buckets + overview for a range
GET  /api/v1/developer/api-logs                # one page of request logs
GET  /api/v1/developer/api-logs/stream         # SSE aggregate snapshots
GET  /api/v1/developer/api-logs/{event_id}     # one request's detail

GET  /api/v1/developer/mcp/connections         # authorized connections + activity
GET  /api/v1/developer/mcp/summary             # header counts
GET  /api/v1/developer/mcp/activity            # one page of tool invocations
POST /api/v1/developer/mcp/connections/{id}/pause
POST /api/v1/developer/mcp/connections/{id}/resume
POST /api/v1/developer/mcp/connections/{id}/revoke
```

### Filters

Shared by the metrics query and the log table, so narrowing the table narrows
the chart identically:

`range` (`5m|15m|1h|24h|7d|30d`), `api_key_id`, `method`, `status` (exact code
or class, e.g. `500` / `5xx`), `route` (substring on the route **template**),
`request_id` (exact), `outcome` (`success|error`), `limit`, `cursor`.

MCP activity takes `range`, `connection_id`, `tool_name`, `outcome`, `limit`,
`cursor`.

### Pagination

Keyset on `(created_at, id)` descending. An offset page would drift as new
requests arrive — a row could be seen twice or skipped as the window shifts —
which for an append-only log is a correctness bug, not a cosmetic one. The
cursor is opaque base64 and a malformed one is treated as the first page rather
than a 400.

### Validation

An unknown range, metric, outcome or method is rejected with `400` and a
machine-readable `code` (`INVALID_RANGE`, `INVALID_METRIC`, `INVALID_OUTCOME`,
`INVALID_METHOD`). Nothing is silently defaulted: defaulting would render one
setting's data under another setting's label.

---

## 9. Retention

- `TELEMETRY_RETENTION_DAYS` (default **30**) bounds both tables.
- The **cleanup worker** deletes rows past the window on its normal interval,
  using an indexed `DELETE` on `created_at`. Telemetry retention rows appear in
  the worker's per-cycle summary line.
- Nothing is archived first: this is metadata, and the product's promise is a
  bounded window rather than indefinite history.
- `TELEMETRY_MAX_RANGE_DAYS` (default 30) bounds the largest query, so a single
  call can never scan an unbounded table.

## 10. Database

Migration **`0025_developer_observability`** (revises `0024_mcp_oauth`):

- `api_request_events` + four indexes, each the leading edge of a query the
  dashboard actually runs: `(account_id, created_at)`,
  `(account_id, api_key_id, created_at)`,
  `(account_id, status_code, created_at)`, `(request_id)`.
- `mcp_tool_invocations` + four indexes:
  `(account_id, created_at)`, `(account_id, grant_id, created_at)`,
  `(account_id, tool_name)`, `(account_id, outcome, created_at)`.
- `mcp_agent_grants.paused_at` (`timestamptz`, nullable) — the audit timestamp
  beside the existing `status` column, which is a plain `VARCHAR` and already
  accepts `PAUSED`, so **no enum change and no enum migration is needed**.

The migration is idempotent (guarded `create_table` / `add_column`) and
reversible. Apply with the normal `RUN_MIGRATIONS=true` startup or
`alembic upgrade head`.

Indexes are chosen against the real query shapes rather than added per column:
each one also costs a write on the ingestion path.

## 11. Testing

```
.venv/bin/python -m pytest -q          # backend
cd web && npm test                     # frontend
.venv/bin/python -m pyrefly check      # type gate
cd web && npm run lint && npm run build
```

Covered, among others: outcome classification at its boundaries; bucket sizing,
alignment and the bucket-count ceiling; percentile interpolation; zero-filling;
instrumentation of success, failure and unhandled-exception paths; correct API
key attribution; that sensitive material has no field to reach; that a failing
sink cannot change a response; every exclusion; account isolation on reads, on
filters, on cursors and on mutations; keyset pagination visiting each row
exactly once; pause blocking an already-issued token; resume not widening
scopes; revocation being terminal; and one user's pause leaving another user's
connection untouched.

The migration is tested in isolation (both tables, every index, the
`paused_at` column, idempotency, reversibility, and that existing grant rows are
untouched), because the full alembic chain cannot run on SQLite — earlier
revisions use PostgreSQL-only `ALTER TYPE ... ADD VALUE`.

**Known gap:** the SSE endpoint's *frame contents* cannot be asserted through the
in-process test transport (reading an infinite `text/event-stream` body blocks
until the connection closes). The frame builder is unit tested directly and the
endpoint's authentication and validation guards are integration tested; only the
byte-level transport is exercised manually.

### A layout trap worth recording

Both tables put their last column's name on the `<th>`'s `aria-label` rather
than in an `sr-only` span. Tailwind's `sr-only` is `position: absolute`, and an
absolutely positioned element inside a horizontally scrolling (`overflow-x:
auto`) table **escapes the scroll container and widens the whole document** —
measured at 361px of page overflow on a 390px viewport, with `body` reporting a
clean width and only `documentElement.scrollWidth` revealing it. Verified after
the fix: zero horizontal overflow at 390 / 768 / 1024 / 1440 on both pages.

## 12. Configuration

See the "Developer observability" block in `.env.example`. The two switches an
operator is most likely to need:

- `TELEMETRY_ENABLED=false` — stop writing new events without removing the
  endpoints or the existing history.
- `TELEMETRY_SSE_MAX_CONNECTIONS` — raise or lower the per-process live-stream
  ceiling.
