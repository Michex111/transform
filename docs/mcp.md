# MCP server (AI agent access)

Transform exposes a **remote MCP server** so an authorized AI agent can work
with a user's documents: find a file, convert it, save the result to the Drive.

```
                Transform
                    │
             Application layer          ← the only place the rules live
      ┌─────────────┼─────────────┐
   FastAPI        MCP          Workers
   /api/*      /mcp (ASGI mount)   converters
      │             │
  SDK clients   AI agents
```

The MCP layer calls the **same application services** the REST API does
(`FileService`, `ConversionService`, `TransferService`). It never touches the
database, Redis or object storage directly, and it re-implements no
authorization rule.

## Endpoints

| Path | Purpose |
|---|---|
| `/mcp` | Streamable HTTP transport (JSON-RPC). Requires `Authorization: Bearer`. |
| `/mcp/authorize` | OAuth 2.1 authorization endpoint (redirects to the consent screen). |
| `/mcp/token` | Token endpoint (authorization code + PKCE, refresh). |
| `/mcp/register` | Dynamic client registration (RFC 7591). |
| `/mcp/revoke` | Token revocation (RFC 7009). |
| `/mcp/.well-known/oauth-authorization-server` | RFC 8414 authorization-server metadata. |
| `/.well-known/oauth-protected-resource{/mcp}` | RFC 9728 protected-resource metadata (served by the API at the origin root). |
| `/.well-known/oauth-authorization-server/mcp` | RFC 8414 metadata at the *inserted* path, so either client derivation resolves. |
| `/api/v1/mcp/authorize` | Consent data (GET) and decision (POST). Authenticated. |
| `/api/v1/mcp/connected-apps` | List / revoke connected applications. Authenticated. |
| `/api/v1/mcp/upload` | Single-use attachment upload (`PUT` raw bytes or `POST` multipart) and the browser page. The capability is the authority. |
| `/api/v1/mcp/upload/info` | Allowed size and type for a link, without consuming it. |

## Authorization model

* **OAuth 2.1 with PKCE (S256)**, resource indicators (RFC 8707) and dynamic
  client registration. `transform-api` is both the resource server and the
  authorization server.
* **No token passthrough.** Tokens are opaque, minted by this server, and bound
  to this server's audience. `validate_token_resource` plus the service's own
  resource check means a token issued for any other resource is rejected.
* **Immediate revocation.** Access tokens are resolved through the grant row on
  *every* request, so revoking an application in Settings stops its agent on the
  very next call — there is no expiry window.
* **Scopes**, granted per application through the consent screen:

  | Scope | Grants |
  |---|---|
  | `documents.read` | list / inspect files, folders and conversions; read file contents |
  | `documents.convert` | start a conversion |
  | `documents.write` | save or upload files, create folders |
  | `documents.delete` | permanently delete a file |
  | `workspaces.read` | see the user's cloud workspaces, their files and execution history |
  | `workspaces.execute` | write files in a workspace and run code there |

  `documents.delete` is never pre-selected, is never in the default scope set,
  and is not implied by any other scope. The `workspaces.*` scopes are separate
  from `documents.*`, are not in the default set, and are never implied by a
  document permission — a workspace grants CPU on the platform, so it is opt-in
  on its own.

  ### `offline_access`

  `offline_access` is advertised in `scopes_supported` and accepted in a request,
  but it is a **request marker, not a permission**. A client sends it to say
  "issue me a refresh token"; Transform always issues one, so it grants nothing
  that is not already granted. It is deliberately *not* an `AgentScope`:

  * `normalize_scopes` only ever yields capabilities, so the marker cannot reach
    a grant row, a token record, or a checkbox on the consent screen;
  * `KNOWN_REQUEST_SCOPES` — the advertised and accepted vocabulary — is the
    capabilities plus the marker; `ALL_SCOPES` remains the capabilities alone.

  Advertising it matters because several clients (ChatGPT among them) decide
  whether to attempt a refresh by looking for this scope, and without it they
  conclude refresh is unavailable and drop the connection when the 60-minute
  access token expires — despite the refresh token having been issued and
  working. Both halves are needed: advertising it while `resolve_scopes` still
  refused it as unrecognised would make every authorization request fail.

  A client that omits `scope` when it registers (RFC 7591 makes the field
  optional, and most clients omit it) is registered against the FULL set rather
  than the default set. It must be: the registered value is then treated as the
  client's own cap, so filling in the consent default made `documents.delete` and
  both `workspaces.*` scopes impossible to grant at consent. The conservative
  default still applies where it belongs — an authorization request that names no
  scope resolves to read/convert/write in `MCPAccessService.resolve_scopes`.
* **Fail closed.** An unrecognised scope string is refused rather than dropped;
  a redirect URI must match a registered value exactly; a resource indicator
  that is not this server is an error.

## Browser authorization flow

An agent cannot approve itself, so the connection is finished by a human in a
browser. Three parts of that are load-bearing:

1. **The API redirects to the SPA consent route**, not to a page of its own
   (`MCP_CONSENT_PATH`, default `/app/authorize`). The signed-in session is a
   bearer token held by the SPA, so a top-level navigation to the API could not
   carry it. The API stays authoritative: the page only relays a decision, and
   every value is re-validated before a code is minted.
2. **The request survives sign-in.** A harness opens the route in a fresh tab, so
   the visitor is usually signed out and is bounced to `/login` first. The
   authorization request has to be restored afterwards, and the sign-in page says
   an application is waiting — a bare form gives no clue why it appeared. The
   recorded destination is validated before use (`lib/returnTo`), because it is
   used as a navigation target and arrives from whatever URL opened the tab.
3. **The handoff is confirmed rather than silent.** Approving records the grant,
   then shows a dialog naming the application with a control that sends the
   browser to its `redirect_uri`. Redirecting immediately would drop the visitor
   on the browser's connection-error page whenever the client's loopback listener
   had stopped — turning a successful connection into what looks like a failure,
   with nothing on screen saying the grant exists.

The consent route must keep a built HTML shell, because a harness opens it as a
cold hard load (see `vite-plugins/spa-route-stubs.ts`).

## Tools

35 tools in two groups. `tools/list` is authoritative for schemas.

**Documents** — gated by `documents.*`:

| Tool | Scope | Writes | Notes |
|---|---|---|---|
| `get_supported_conversions` | read | no | the conversion graph (no folder gate: no user data) |
| `list_files` | read | no | whole Drive; optional name/extension/folder filter |
| `list_folders` | read | no | folder ids — nothing else returns them |
| `get_file` | read | no | metadata only — never content or a storage key |
| `read_file_text` | read | no | **read a document's text**; extracts PDF/DOCX/XLSX; paged by characters |
| `download_file` | read | no | raw bytes as base64, **paged** |
| `read_conversion_output` | read | no | read a completed job's output as text without saving it |
| `get_file_download_link` | read | no | short-lived browser link for a Drive file |
| `get_conversion_status` | read | no | poll an async conversion |
| `get_conversion_history` | read | no | recent conversions (AGENT scope = this connection) |
| `get_credits` | read | no | remaining credits (no folder gate: no user data) |
| `convert_file` | convert | creates a job | the original is never modified |
| `save_file` | write | creates a file | only promotes a job Transform itself produced |
| `upload_file` | write | creates a file | `content_text` (UTF-8) or `content_base64`; size-capped |
| `create_upload_link` | write | authorizes an upload | single-use link for an attachment's real bytes; not an upload itself |
| `get_upload_status` | read | no | whether that upload landed, and the job it started |
| `import_file_from_url` | write | creates a file | https-only server-side fetch; disabled unless `MCP_URL_IMPORT_ENABLED` |
| `create_folder` | write | creates a folder | the only structural change an agent may make |
| `delete_file` | delete | deletes | irreversible; needs an explicit grant |

**Workspaces** — gated by `workspaces.*`:

| Tool | Scope | Writes | Notes |
|---|---|---|---|
| `list_workspaces` | workspaces.read | no | the user's workspaces (paged) |
| `get_workspace` | workspaces.read | no | runtime state, toolchain, disk budget, latest environment |
| `get_workspace_environment` | workspaces.read | no | whether the saved snapshot is ready |
| `list_workspace_files` | workspaces.read | no | files inside a workspace |
| `read_workspace_file` | workspaces.read | no | text or `encoding="base64"`; paged by byte |
| `list_artifacts` | workspaces.read | no | files recent runs produced |
| `get_execution` | workspaces.read | no | status, exit code, paged stdout/stderr |
| `write_workspace_file` | workspaces.execute | yes | `content` or `content_base64` |
| `run_code` | workspaces.execute | yes | async — returns an execution id |
| `cancel_execution` | workspaces.execute | yes | stop a run that is taking too long |
| `save_workspace_environment` | workspaces.execute | yes | persist installed packages |
| `start_workspace` | workspaces.execute | yes | start the runtime (file tools do this automatically) |
| `stop_workspace` | workspaces.execute | yes | stop the runtime, keeping files |
| `create_workspace` | workspaces.execute | yes | only when the user asks for a new one |
| `copy_file_to_workspace` | documents.read + workspaces.execute | yes | bring a Drive file in for code |
| `save_artifact_to_drive` | workspaces.read + documents.write | creates a file | save a produced artifact |

Deliberate omissions:

* **No way to name a storage object.** Every tool takes a `file_id` / `job_id`
  and resolves it server-side, so an agent can never name an object it was not
  authorized to read. No storage key or bucket is accepted. A completed job may
  return a short-lived download capability, not a presigned storage GET.
* **No folder or workspace mutation beyond creation.** An agent may create a
  folder or a workspace; renaming, moving and deleting either are not exposed.
  Deleting a workspace is a decision a human makes in the app.
* **Uploads are bounded and content-carrying.** `upload_file` is the only tool
  that introduces content the user did not already have, and it caps the decoded
  bytes before anything touches storage.

### Reading a large file

A tool result travels as a single Streamable-HTTP server-sent event, which the
MCP transport caps at 1 MiB, and a `structured_output` tool puts the payload in
that event twice. `download_file` therefore returns at most 128 KiB of file
content per call and reports `has_more` / `next_offset` so the caller can
continue; the caller concatenates the decoded parts in order.

Each call reads only the page it needs (plus one byte to distinguish "more
follows" from "that was the end"), not the whole object. For encrypted files the
at-rest format's chunking means the read reaches the end of the chunk containing
the page; it never reassembles the file. `size_bytes` is the whole file's size
while more pages follow, taken from the file row.

This replaced a tool that advertised a 25 MiB limit and in practice failed at
roughly 340 KiB with `SSE stream failed: Server-sent event exceeded the 1048576
byte limit` — an ordinary 1 MB PDF was unreadable, and the error named no file
and suggested no next step. Measured live: 256 KiB succeeded, 384 KiB failed.

### Reading a document (not bytes)

`download_file` is the wrong tool for "summarise this PDF": a model cannot
decode base64 in its head, and PDF/DOCX/XLSX bytes are a container format. Use
`read_file_text` (or `read_conversion_output` for a finished job). Text formats
are decoded directly; container formats are extracted through the same converter
functions the workers use, in-process, creating no job and charging no credits.
Results are cached per `(extension, sha256(content))`, and pages are sized by
characters (`MCP_TEXT_PAGE_CHARS`).

## Attachments: getting the real bytes to the server

An MCP client's chat attachment never reaches this server. The model is shown
extracted text and image previews, not the original bytes, so it cannot rebuild
a PDF, DOCX, XLSX or image for `upload_file(content_base64)`. Text attachments
already work through `upload_file(content_text)`.

The byte path is a **short-lived, single-use capability**:

1. The agent calls `create_upload_link(file_name, size_bytes?, folder_id?,
   target_format?)`. This authorizes one upload and returns `upload_url`,
   `upload_header_name` (`X-Transform-Upload`), `upload_capability`,
   `curl_command` and `upload_page_url`. It does not upload anything.
2. The client's code sandbox runs `curl_command` with the real file, or the user
   opens `upload_page_url` and drops the same file into it.
3. The agent polls `get_upload_status(upload_id)` until `status` is `COMPLETED`.
   When `target_format` was requested, the same response carries the conversion
   `job_id`, and a completed job yields the usual short-lived `download_url`.

The capability is bound to the **grant**, not to the access token, so an hourly
token refresh does not kill a pending upload. Every redemption re-reads the
grant: a revoked, paused or narrowed connection, or one re-bound to a folder
that does not contain the destination, refuses with one generic sentence
(`This upload link is unavailable or has expired.`) so the link is not an oracle.
Replay is refused because redemption is a single conditional
`UPDATE … WHERE used_at IS NULL`.

Nothing about the normal pipeline is weakened. The bytes go through
`create_upload` → storage write → `verify_upload_completion` →
`FileService.complete_upload`, so the tier per-file cap, the account quota, the
extension and magic-byte checks, and folder ownership all apply. The effective
ceiling is `min(tier cap, MCP_ATTACHMENT_MAX_BYTES)`.

The capability is never in a query string: `curl` sends it in the
`X-Transform-Upload` header, and the browser page reads it from the URL
**fragment** (which browsers do not send to servers) and clears it from history
before uploading. Only a 12-character SHA-256 fingerprint is ever logged.

### Settings

| Setting | Default | Meaning |
|---|---|---|
| `MCP_ATTACHMENT_MAX_BYTES` | 100 MiB | Largest file the MCP path accepts, before the tier cap |
| `MCP_UPLOAD_LINK_TTL_SECONDS` | 900 | How long a link (and its intent) stays usable |
| `MCP_UPLOAD_RATE_LIMIT` | 60 | Requests/minute per capability fingerprint (fallback IP) |
| `MCP_URL_IMPORT_ENABLED` | false | Whether `import_file_from_url` is available |

`/api/v1/mcp/upload` is exempt from `MAX_REQUEST_BODY_BYTES` because the body is
the file; it enforces its own streamed ceiling and keeps a `Content-Length` fast
path against `MCP_ATTACHMENT_MAX_BYTES`. CORS is open on that one path only
(any origin, no credentials — the capability is the whole authority), and the
rate limit is keyed by capability rather than the anonymous IP bucket, because
hosted code sandboxes share egress addresses.

### Client setup note

A sandbox that cannot reach the API's domain will report a network/proxy error
from `curl`. That is a client-side network policy, not a broken link: allow the
API domain in the client's code-execution network settings, or use
`upload_page_url` instead. The agent must not claim an upload happened until
`get_upload_status` says `COMPLETED`.

`import_file_from_url` is off by default. When enabled it fetches `https:` only,
resolves DNS and refuses private, loopback, link-local, multicast, reserved and
cloud-metadata ranges on every redirect (at most three), pins the resolved
address, uses a 15 s connect/read timeout, streams with the same size cap, and
forwards no cookies or authorization headers.

## Authorization: the grant is the authority

An access token is a **handle**; the stored `AgentGrant` is the permission.
`load_access_token` resolves the effective scope set from the grant on every
request, so an edit on Settings → AI apps takes effect on the agent's next call
in both directions. Narrowing reduces the token's powers rather than rejecting
it (rejecting made clients loop refresh → 401 and show the connection as
broken). Widening needs no reconnect.

The consent screen offers every scope the *client registered for* intersected
with the deployment's `grantable_scopes`, not only the scopes the authorization
request named. `requested_scopes` decides what is pre-checked; destructive
scopes are never pre-checked. This is what makes `workspaces.*` and
`documents.delete` grantable from clients that omit `scope` at registration
(most of them).

Refresh rotation is atomic (a single conditional `UPDATE … WHERE revoked_at IS
NULL`) and has a reuse grace window (`MCP_REFRESH_REUSE_GRACE_SECONDS`, default
60). Presenting a just-rotated refresh token inside that window returns a fresh
pair and retires the abandoned successor, so a lost response or a retried request
does not disconnect the client. After the window, a replay is `invalid_grant` as
before.

## Step-up authorization

A missing scope is returned as a tool value
(`{"ok": false, "required_scope": "…", "reconnect_required": true}`). The MCP
SDK's transport does not expose a per-request hook for a `403` with
`WWW-Authenticate: Bearer error="insufficient_scope"`, so the server cannot start
re-consent by itself; the agent's recovery path is to ask the user to reconnect.
Because the consent screen now lists every available scope (above), reconnecting
actually offers the missing one.

## Rate limits

`/mcp/token`, `/mcp/register`, `/mcp/revoke` and `/mcp/authorize` carry no bearer
token. They have their own bucket keyed by `(client_id, client IP)` at
`MCP_OAUTH_RATE_LIMIT` (default 240/min), so hosted clients sharing egress
addresses do not throttle unrelated users — a 429 on refresh disconnects the
client. A 429 includes `Retry-After`. The API is deployed with
`--proxy-headers --forwarded-allow-ips=*` so `client.host` is the real client
behind Render's proxy.

## Housekeeping

Expired codes and tokens are purged by a jittered lifespan task every
`MCP_HOUSEKEEPING_INTERVAL_SECONDS` (default 3600). Without it the token table
grew by two rows per hour per connection, forever.

## Asynchronous conversions

The SDK has no tasks API, so conversions follow the product's existing model:
`convert_file` returns a `job_id` immediately, and the agent polls
`get_conversion_status` until the status is `COMPLETED` or `FAILED`. Nothing
blocks on a Worker.

Currently `convert_file` requires an existing Drive file. `upload_file` creates
a Drive file from `content_text` (UTF-8) or `content_base64` (bytes); a chat
attachment being visible to the model does not make its bytes available to this
remote MCP server. If the client cannot provide the content, ask the user to
upload the file in Transform. The server does not fetch arbitrary URLs.

When an owned completed job is authorized for download, `get_conversion_status`
also returns top-level `download_url` and `expires_in_seconds` (300). The link
opens in a JavaScript-enabled browser and streams plaintext, including outputs
encrypted at rest; the browser needs no OAuth token. It is a **bearer
capability**: share it only with the intended user. Poll again for a fresh link
if it expires. Token expiry, revocation, pausing, scope changes, and folder
access are rechecked on redemption; an already-started stream is not cancelled.

No link is returned for a pending or failed job or when authorization cannot
be established. For a folder-restricted connection the source must resolve to
an owned Drive file currently in the allowed subtree; staging jobs without
durable folder attribution are denied. `download_available` on the existing
job payload does not guarantee a link was issued. A user can also ask to
`save_file` and download the result in Transform. Never construct a link from
a storage key or put an OAuth token in a URL.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `true` | Serve the MCP endpoint and its OAuth surface. |
| `MCP_RESOURCE_URL` | `http://localhost:8000/mcp` | Public URL of the endpoint. **Also the OAuth issuer and the token audience** — production boot refuses a non-`https` value. |
| `MCP_ACCESS_TOKEN_TTL_MINUTES` | `60` | Access-token lifetime. |
| `MCP_REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh-token lifetime. |
| `MCP_AUTHORIZATION_CODE_TTL_MINUTES` | `5` | PKCE code lifetime. |
| `MCP_REFRESH_REUSE_GRACE_SECONDS` | `60` | Window in which a just-rotated refresh token is accepted as a retry. `0` disables it. |
| `MCP_OAUTH_RATE_LIMIT` | `240` | Requests/minute for the unauthenticated OAuth endpoints, per `(client_id, IP)`. |
| `MCP_HOUSEKEEPING_INTERVAL_SECONDS` | `3600` | How often expired codes and tokens are purged. |
| `MCP_TEXT_PAGE_CHARS` | `24000` | Maximum characters one `read_file_text` page may return. |
| `MCP_TEXT_EXTRACTION_MAX_BYTES` | `26214400` (25 MiB) | Largest document text extraction will read. |
| `MCP_CONSENT_PATH` | `/app/authorize` | SPA route for the consent screen. |
| `MCP_MAX_REQUEST_BODY_BYTES` | `41943040` (40 MiB) | Largest request body the transport accepts. Larger than the REST cap because `upload_file` carries a 25 MiB file as ~33.4 MiB of base64 inside one JSON-RPC request; refused on `Content-Length` before the body is read. |

Browser-based MCP clients also need their origin in `ALLOWED_ORIGINS`.

## Deployment notes

* The endpoint is **stateless** (`stateless_http=True`): no `Mcp-Session-Id`,
  no sticky sessions, no shared session store. Any number of API instances can
  serve it behind a plain load balancer.
* The SDK's default DNS-rebinding allowlist (localhost only) is disabled
  explicitly — on a real domain it would answer every request with
  `421 Misdirected Request`. Every request must carry a bearer token bound to
  this audience, so there is no ambient credential for such an attack to abuse.
* The Streamable HTTP session manager may only be started once per instance, so
  the MCP server is built per lifespan (`LazyMCPMount`) rather than at import
  time.
* `/mcp` is covered by the same rate limiter as `/api/*` and is keyed by the
  bearer token's hash.
* Every tool call emits `log_data_access` (`action="mcp.tool_call"`); every
  refusal emits `log_permission_denied`. Both carry `client_id` and `grant_id`
  so an incident can be attributed to one application.

## Tests

| File | Covers |
|---|---|
| `tests/unit/application/test_mcp_access_service.py` | scopes, redirect matching, audience binding, code single-use, refresh rotation, revocation, grant narrowing |
| `tests/unit/application/test_mcp_toolbox.py` | scope gating, ownership delegation, no storage keys in results, `save_file` rules |
| `tests/integration/test_mcp_oauth_flow.py` | discovery documents, DCR → consent → PKCE token exchange, tool calls over the real transport, cross-user isolation (IDOR), immediate revocation |
| `tests/unit/infrastructure/test_migration_0024_mcp_oauth.py` | the schema's tables, indexes, idempotency and reversibility |
| `tests/unit/presentation/test_mcp_workspace_tools.py` | every workspace tool's scope gate, the workspace port's deliberate lack of `delete_workspace`, and that `run_code` never claims completion |
