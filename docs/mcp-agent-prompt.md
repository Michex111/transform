# Connecting an agent to the Transform MCP server

The prompt itself lives in one place and is served to users:

- **Rendered on the site:** `https://transform-to.com/mcp`, section *"Hand your
  agent the instructions"*, as a copyable block.
- **Source of truth:** [`web/src/lib/mcpAgentPrompt.ts`](../web/src/lib/mcpAgentPrompt.ts)
  (`MCP_AGENT_PROMPT`, `MCP_ENDPOINT`).

It is deliberately **not** duplicated here. Two copies of a 160-line instruction
set drift, and the wrong one wins silently — which for connection instructions
means an agent that cannot connect and a failure that looks like a broken server.
[`mcpAgentPrompt.test.ts`](../web/src/lib/mcpAgentPrompt.test.ts) pins the facts a
connection cannot be made without (endpoint, discovery paths, PKCE, the
public-client method, every scope and tool name), so dropping one fails the build
rather than shipping unusable instructions.

To change the prompt, edit the TypeScript module.

---

## Operator notes

Not shown to users. These are the things worth knowing when someone reports
"the agent can't connect".

### `MCP_RESOURCE_URL` must be set on the API service

It is the token audience **and** the OAuth issuer. Unset, the server advertises
`http://localhost:8000/mcp` and **no remote client can connect** — discovery
succeeds, and then every endpoint it points at is unreachable.

    MCP_RESOURCE_URL=https://transform-api-7b3g.onrender.com/mcp

Production **warns** rather than refusing to boot in this case (it only refuses a
non-HTTPS value), so a broken deployment starts up and looks healthy. The warning
reads:

> `MCP_RESOURCE_URL is not set, so the MCP endpoint advertises
> 'http://localhost:8000/mcp' as its audience and OAuth issuer. Remote AI clients
> will not be able to connect.`

Check for that line first. Confirm from outside with:

    curl -s https://transform-api-7b3g.onrender.com/.well-known/oauth-protected-resource/mcp

`resource` and `authorization_servers` must both be the public HTTPS origin.

### `offline_access` is not advertised

Transform issues refresh tokens and supports the `refresh_token` grant, but the
scope is absent from `scopes_supported`. A client that looks for it — ChatGPT
does — may conclude refresh is unavailable and lose the connection when the
60-minute access token expires.

**Do not simply add it to the metadata.** `MCPAccessService.resolve_scopes` raises
on any unrecognised scope (a fail-closed rule that stops "grant read" silently
becoming something broader), so advertising `offline_access` without also
teaching that resolver to accept it as a *marker* rather than a capability would
make every authorization request fail. Both halves or neither.

### The metadata under-reports auth methods

`token_endpoint_auth_methods_supported` lists only `client_secret_post` and
`client_secret_basic`. That list is **hardcoded in the MCP SDK**
(`mcp/server/auth/routes.py`), not chosen by Transform. Public clients using
`token_endpoint_auth_method: "none"` register and work correctly — verified — but
a strict client could refuse to register as one on the strength of the metadata
alone.

### `/mcp` answers `307` to `/mcp/`

A client that does not follow redirects on POST will fail here. The prompt warns
about it; the trailing-slash form avoids the redirect entirely.

### MCP Activity was empty — fixed, and how it broke

The write path was built without a `session_factory`, so
`TelemetryIngestion._write` took its **"no storage wired"** branch: it counted
each batch as written and returned. Both `mcp_tool_invocations` and
`api_request_events` were affected, so **API Logs was empty too**.

That failure was invisible by construction, which is the part worth remembering:

| Signal | Read as | Actually meant |
| --- | --- | --- |
| `telemetry_ingest_written_total` rising | telemetry working | batches counted, never stored |
| `dropped` / `write_failures` at `0` | nothing going wrong | nothing was being attempted |
| Activity page empty | "no agent has called us yet" | the audit trail was not recording |

The fix is one line — `session_factory=lambda: get_session_factory()()` in
[`ingestion.py`](../src/infrastructure/telemetry/ingestion.py) — and the **double
call is deliberate**: the field wants a zero-arg callable returning an async
context manager, and `get_session_factory()` returns a *sessionmaker*. Passing
the accessor itself fails on the first flush with `async_sessionmaker object does
not support the asynchronous context manager protocol`, which shows up as
`write_failures` climbing while `written` stays flat.

Regression test:
[`test_telemetry_ingestion_wiring.py`](../tests/unit/infrastructure/test_telemetry_ingestion_wiring.py)
pins both halves (a factory is present, and calling it yields an async context
manager).

The MCP tool wrapper also **logs** a dropped invocation now, instead of
`except Exception: return`. A silent swallow is indistinguishable from "nobody
called the tools", which is what made this take a trace to find. Look for:

> `MCP invocation telemetry dropped for <tool>`

To confirm the path is live, compare `telemetry_ingest_written_total` before and
after one authenticated call: it must rise *and* the row must appear on the
Activity page. A rising counter alone is not evidence — that is exactly the trap.

### The consent route is opened cold by a harness

A harness opens `/app/authorize?...` in a **fresh browser tab**, normally with no
session. Two things have to be true for that to work, and the second was broken:

1. **The route needs a built HTML shell**, or the CDN 404s it before the SPA ever
   loads. `vite-plugins/spa-route-stubs` writes one per route, and it discovers
   routes by reading **string literals** out of `App.tsx` — so the path must stay
   `path="/app/authorize"`. (Substituting the `MCP_AUTHORIZE_ROUTE` constant
   silently removed the stub; `spa-route-stubs.test.ts` catches this.)
2. **Signing in must return the visitor to the authorization request.**
   `PublicOnlyRoute`, which wraps `/login`, redirected to a hardcoded
   `/app/dashboard`. Because it fires the instant authentication succeeds, it beat
   the sign-in page's own navigation to the recorded destination every time — so
   the request was discarded and the connection could never complete. It now
   navigates to the destination `ProtectedRoute` recorded, validated by
   `lib/returnTo.safeReturnPath` (the value is a navigation target and arrives
   from whatever URL opened the tab, so an absolute URL there would be an open
   redirect).

    This also silently affected Stripe's `/app/billing?credits=success` return and
    every signed-out deep link.

Symptom if this regresses: the agent says "authenticate in the browser", the
visitor signs in, lands on the dashboard, and the agent waits until it times out.

### After approval, the visitor completes the handoff

Approving records the grant, then shows a dialog naming the application with a
**Return to `<app>`** control that performs the redirect. The handoff is not
automatic because the destination is usually a loopback port: if that listener
has stopped, an automatic redirect replaces a successful connection with the
browser's connection-error page. The dialog therefore states that the connection
is already saved and tells the visitor to switch back themselves.

The authorization code lives 5 minutes (`MCP_AUTHORIZATION_CODE_TTL_MINUTES`), so
a visitor who leaves the dialog open for longer must reconnect; the grant itself
is unaffected.

### Verifying a connection without a real agent

The flow is `register → authorize → token → initialize → tools/call`. Consent can
also be approved **over HTTP**, with no browser and no human, by signing in and
posting to the same endpoint the consent page uses:

    POST /api/users/token                      # session JWT for the account
    POST /mcp/register                         # -> client_id
    GET  /api/v1/mcp/authorize?client_id=...&redirect_uri=...&scope=...&code_challenge=...
    POST /api/v1/mcp/authorize                 # -> redirect_url containing ?code=
    POST /mcp/token                            # -> access_token

That makes the whole path scriptable, which is worth doing whenever the question
is "is the *server* working" rather than "does this particular agent work".

For a real agent, authorize once and reuse the refresh token rather than
re-running consent each time.
