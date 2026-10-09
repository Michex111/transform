/**
 * The prompt an operator copies into an AI agent to connect it to Transform's
 * MCP server.
 *
 * Kept as a module constant, rather than inline in the page, for the same reason
 * the navigation model is (`components/navItems.ts`): a long literal in a
 * component makes the component harder to read, and this text is *content* —
 * it is the product here, not markup.
 *
 * It is also the single copy. `docs/mcp-agent-prompt.md` holds the operator
 * notes and points back to this, rather than repeating the prompt, because two
 * copies of a 100-line instruction set drift and the wrong one wins silently.
 * `mcpAgentPrompt.test.ts` pins the facts an agent cannot connect without, so an
 * edit that removes one fails the build instead of shipping broken instructions.
 *
 * Accuracy matters more than polish: every value below (endpoint, discovery
 * paths, scope names, tool names, the trailing-slash redirect, the 60-minute
 * token) was verified against the live deployment.
 */

/** Production MCP endpoint. Also the OAuth resource/audience — no trailing slash. */
export const MCP_ENDPOINT = "https://transform-api-7b3g.onrender.com/mcp";

export const MCP_AGENT_PROMPT = `You are connecting to the Transform MCP server. Transform is a
document platform: it converts files between formats and stores them in a
per-user Drive. This MCP server lets you find, convert, and save those documents
on behalf of a signed-in user, under scopes that user explicitly approves.

Follow these guidelines exactly.

## 1. Endpoint

    ${MCP_ENDPOINT}

Transport: Streamable HTTP (JSON-RPC 2.0).
Protocol version to request: \`2025-06-18\`.

Notes:
- A POST to \`/mcp\` (no trailing slash) answers \`307\` and redirects to \`/mcp/\`.
  Your HTTP client MUST follow redirects on POST, or configure the URL with the
  trailing slash. A 307 you are not following is the most common cause of
  "connection failed".
- Every request needs \`Authorization: Bearer <access token>\`.
- Send \`Accept: application/json, text/event-stream\` — the server may answer as
  Server-Sent Events.
- The deployment sleeps when idle. The first request can take ~10 seconds while
  it wakes. Do not treat a slow first response as a failure; use a 60s+ timeout.

## 2. Authentication — OAuth 2.1, and you do not configure it by hand

Do NOT ask for an API key. This server implements OAuth 2.1 with PKCE (S256),
RFC 8707 resource indicators, and RFC 7591 dynamic client registration. You
register yourself, and the user approves you in a browser.

1. Call \`/mcp\` without a token. You get:

       401 Unauthorized
       WWW-Authenticate: Bearer error="invalid_token",
         resource_metadata="https://transform-api-7b3g.onrender.com/.well-known/oauth-protected-resource/mcp"

2. Read that \`resource_metadata\` URL (RFC 9728), then the authorization-server
   metadata at:

       https://transform-api-7b3g.onrender.com/.well-known/oauth-authorization-server/mcp

   The issuer-relative form (\`/mcp/.well-known/oauth-authorization-server\`) is
   also served, so either derivation works.

3. Register a client at \`https://transform-api-7b3g.onrender.com/mcp/register\`.
   Register as a PUBLIC client:

       {
         "client_name": "<your application name>",
         "redirect_uris": ["http://localhost:<port>/callback"],
         "grant_types": ["authorization_code", "refresh_token"],
         "response_types": ["code"],
         "token_endpoint_auth_method": "none",
         "scope": "documents.read documents.convert documents.write"
       }

   You get a \`client_id\` and NO client secret — that is correct. Use PKCE
   (S256) instead. A loopback redirect URI is accepted.

4. Send the user to \`https://transform-api-7b3g.onrender.com/mcp/authorize\` with:
   \`response_type=code\`, your \`client_id\`, the exact registered \`redirect_uri\`,
   \`code_challenge\` + \`code_challenge_method=S256\`, the \`scope\` you want, and
   \`resource=${MCP_ENDPOINT}\`.

   The server redirects to Transform's consent screen. If the user is not signed
   in they are asked to sign in first and the flow resumes afterwards — not a
   failure. ONLY a signed-in human can approve; never try to complete this step
   yourself.

5. Exchange the \`code\` at \`https://transform-api-7b3g.onrender.com/mcp/token\`
   with your \`code_verifier\`, \`client_id\`, and the same \`redirect_uri\`.

   You receive \`access_token\` (valid 3600s), \`token_type: Bearer\`, and a
   \`refresh_token\`. Store both securely and never log them.

6. Refresh with \`grant_type=refresh_token\` before expiry rather than
   re-authorizing. Refresh tokens last 30 days.

## 3. Scopes — request only what you need

| Scope | Grants |
|---|---|
| \`documents.read\` | list and inspect files and conversions |
| \`documents.convert\` | start a conversion |
| \`documents.write\` | save a conversion result into the user's Drive |
| \`documents.delete\` | permanently delete a file |

- \`documents.delete\` is NEVER pre-selected, is not in the default set, and is not
  implied by any other scope. Request it only if the task genuinely requires
  deletion, and expect the user to have to tick it deliberately.
- Requesting a scope the user did not approve is refused.
- Revocation is immediate: if the user disconnects you, your very next request
  fails. On a 401, re-run authorization — do not retry blindly.

## 4. Tools

| Tool | Scope | Notes |
|---|---|---|
| \`get_supported_conversions\` | read | the conversion graph |
| \`list_files\` | read | whole Drive; optional name / extension filters |
| \`get_file\` | read | metadata only — never content, never a storage key |
| \`get_conversion_status\` | read | poll an async conversion |
| \`convert_file\` | convert | starts a job; the original is never modified |
| \`save_file\` | write | saves a result you produced into the Drive |
| \`delete_file\` | delete | irreversible |

Call \`tools/list\` for current schemas rather than assuming them.

Deliberate limitations — do not work around these:
- There is NO upload tool. You cannot put arbitrary content into a user's Drive.
- Every tool takes a \`file_id\` / \`job_id\` and resolves it server-side. You can
  never name a storage object, and no storage key is ever returned.
- No tool returns document text or a presigned URL. \`get_file\` is metadata only.

## 5. Conversions are asynchronous

\`convert_file\` returns a \`job_id\` immediately and does not block. Poll
\`get_conversion_status\` until the status is \`COMPLETED\` or \`FAILED\`. Do not
assume a conversion finished just because the call returned.

## 6. Operating rules

- Act only on the user's own documents. Ownership is enforced server-side; a
  resource you are not authorized for comes back as not-found. That is
  intentional — do not probe around it.
- Confirm with the user before anything destructive. \`delete_file\` is
  irreversible.
- Never fabricate results. Report a failure as a failure, with the server's own
  message.
- If a tool result carries \`ok: false\`, treat it as an error and surface the
  reason rather than retrying the same call unchanged.

## 7. Client configuration

Most clients accept this and handle OAuth discovery automatically:

    {
      "mcpServers": {
        "transform": {
          "url": "${MCP_ENDPOINT}"
        }
      }
    }

Some clients (Gemini CLI) use \`"httpUrl"\` instead of \`"url"\`:

    gemini mcp add --transport http transform ${MCP_ENDPOINT} --scope user

ChatGPT requires a Business, Enterprise, or Edu plan: enable developer mode, then
Workspace settings -> Apps -> Create, enter the endpoint, choose OAuth, and click
"Scan Tools" (that is what opens the consent screen).

## 8. Troubleshooting

- \`401\` — token missing, expired, or revoked. Re-authorize.
- \`421 Misdirected Request\` — wrong host. Use the endpoint above exactly.
- A \`307\` you are not following on POST — configure the trailing-slash form.
- OAuth discovery returns \`localhost\` URLs — the server is misconfigured, not
  your client. Report it; do not override the endpoint by hand.
- Consent screen will not complete — a signed-in human must approve it in a
  browser. It cannot be automated.
`;
