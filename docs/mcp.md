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

24 tools in two groups. `tools/list` is authoritative for schemas.

**Documents** — gated by `documents.*`:

| Tool | Scope | Writes | Notes |
|---|---|---|---|
| `get_supported_conversions` | read | no | the conversion graph |
| `list_files` | read | no | whole Drive; optional name/extension/folder filter |
| `list_folders` | read | no | folder ids — nothing else returns them |
| `get_file` | read | no | metadata only — never content or a storage key |
| `download_file` | read | no | file contents as base64, **paged** |
| `get_conversion_status` | read | no | poll an async conversion |
| `get_conversion_history` | read | no | recent conversions |
| `get_credits` | read | no | remaining credits |
| `convert_file` | convert | creates a job | the original is never modified |
| `save_file` | write | creates a file | only promotes a job Transform itself produced |
| `upload_file` | write | creates a file | the only way to introduce new content; size-capped |
| `create_folder` | write | creates a folder | the only structural change an agent may make |
| `delete_file` | delete | deletes | irreversible; needs an explicit grant |

**Workspaces** — gated by `workspaces.*`:

| Tool | Scope | Writes | Notes |
|---|---|---|---|
| `list_workspaces` | workspaces.read | no | the user's workspaces |
| `get_workspace` | workspaces.read | no | runtime state, toolchain, disk budget |
| `list_workspace_files` | workspaces.read | no | files inside a workspace |
| `read_workspace_file` | workspaces.read | no | read one workspace file |
| `list_artifacts` | workspaces.read | no | files recent runs produced |
| `write_workspace_file` | workspaces.execute | yes | write a file verbatim |
| `run_code` | workspaces.execute | yes | async — returns an execution id |
| `get_execution` | workspaces.read | no | status, exit code, stdout, stderr |
| `cancel_execution` | workspaces.execute | yes | stop a run that is taking too long |
| `save_workspace_environment` | workspaces.execute | yes | persist installed packages |
| `create_workspace` | workspaces.execute | yes | only when the user asks for a new one |

Deliberate omissions:

* **No way to name a storage object.** Every tool takes a `file_id` / `job_id`
  and resolves it server-side, so an agent can never name an object it was not
  authorized to read. No storage key, bucket or presigned URL is ever returned.
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

## Asynchronous conversions

The SDK has no tasks API, so conversions follow the product's existing model:
`convert_file` returns a `job_id` immediately, and the agent polls
`get_conversion_status` until the status is `COMPLETED` or `FAILED`. Nothing
blocks on a Worker.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MCP_ENABLED` | `true` | Serve the MCP endpoint and its OAuth surface. |
| `MCP_RESOURCE_URL` | `http://localhost:8000/mcp` | Public URL of the endpoint. **Also the OAuth issuer and the token audience** — production boot refuses a non-`https` value. |
| `MCP_ACCESS_TOKEN_TTL_MINUTES` | `60` | Access-token lifetime. |
| `MCP_REFRESH_TOKEN_TTL_DAYS` | `30` | Refresh-token lifetime. |
| `MCP_AUTHORIZATION_CODE_TTL_MINUTES` | `5` | PKCE code lifetime. |
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
