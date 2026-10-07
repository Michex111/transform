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
  | `documents.read` | list / inspect files and conversions |
  | `documents.convert` | start a conversion |
  | `documents.write` | save a conversion result into the Drive |
  | `documents.delete` | permanently delete a file |

  `documents.delete` is never pre-selected, is never in the default scope set,
  and is not implied by any other scope.
* **Fail closed.** An unrecognised scope string is refused rather than dropped;
  a redirect URI must match a registered value exactly; a resource indicator
  that is not this server is an error.

## Tools

| Tool | Scope | Writes | Notes |
|---|---|---|---|
| `get_supported_conversions` | read | no | the conversion graph |
| `list_files` | read | no | whole Drive; optional name/extension filter |
| `get_file` | read | no | metadata only — never content or a storage key |
| `get_conversion_status` | read | no | poll an async conversion |
| `convert_file` | convert | creates a job | the original is never modified |
| `save_file` | write | creates a file | only promotes a job Transform itself produced |
| `delete_file` | delete | deletes | irreversible; needs an explicit grant |

Deliberate omissions:

* **No upload tool.** An agent cannot introduce arbitrary content into a Drive.
* **No storage-key arguments.** Every tool takes a `file_id` / `job_id` and
  resolves it server-side, so an agent can never name an object it was not
  authorized to read.
* **No content in tool results.** No tool returns a document's text, a presigned
  URL, a storage key or any infrastructure detail.

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
