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

   After approving, the user confirms the handoff to your \`redirect_uri\` from a
   "…is connected" dialog. The grant is recorded when they approve, so the
   dialog tells them to switch back to your application if the redirect does not
   open it.

5. Exchange the \`code\` at \`https://transform-api-7b3g.onrender.com/mcp/token\`
   with your \`code_verifier\`, \`client_id\`, and the same \`redirect_uri\`.

   You receive \`access_token\` (valid 3600s), \`token_type: Bearer\`, and a
   \`refresh_token\`. Store both securely and never log them.

6. Refresh with \`grant_type=refresh_token\` before expiry rather than
   re-authorizing. Refresh tokens last 30 days, and a refresh token is issued on
   every code exchange — you do not need to do anything special to get one.

## 3. Scopes — request only what you need

| Scope | Grants |
|---|---|
| \`documents.read\` | list and inspect files, folders and conversions; read file contents |
| \`documents.convert\` | start a conversion |
| \`documents.write\` | save or upload files into the user's Drive, create folders |
| \`documents.delete\` | permanently delete a file |
| \`workspaces.read\` | see the user's cloud workspaces, their files and execution history |
| \`workspaces.execute\` | write files in a workspace and run code there |

- \`documents.delete\` is NEVER pre-selected, is not in the default set, and is not
  implied by any other scope. Request it only if the task genuinely requires
  deletion, and expect the user to have to tick it deliberately.
- The \`workspaces.*\` scopes are separate from \`documents.*\` and are not granted
  by default: a workspace grants CPU on the platform, and a workspace is not in
  the Drive, so folder confinement does not apply to it.
- Requesting a scope the user did not approve is refused.
- Revocation is immediate: if the user disconnects you, your very next request
  fails. On a 401, re-run authorization — do not retry blindly.

### \`offline_access\`

You may include \`offline_access\` in the \`scope\` parameter to ask for a refresh
token. It is advertised in \`scopes_supported\` and always accepted, but it is a
**request marker, not a permission**:

- it will NOT appear in the granted scopes, and that is not a refusal;
- a refresh token is issued on every code exchange regardless;
- do not report it to the user as a permission they granted, and do not treat
  its absence from the token response as a failure.

## 4. Tools

| Tool | Scope | Notes |
|---|---|---|
| \`get_supported_conversions\` | read | the conversion graph |
| \`list_files\` | read | whole Drive; optional name / extension / folder filters |
| \`list_folders\` | read | folder ids — nothing else returns them |
| \`get_file\` | read | metadata only — never content, never a storage key |
| \`download_file\` | read | file contents, paged (see below) |
| \`get_conversion_status\` | read | poll an async conversion |
| \`get_conversion_history\` | read | recent conversions |
| \`get_credits\` | read | the user's remaining credits |
| \`convert_file\` | convert | starts a job; the original is never modified |
| \`save_file\` | write | saves a result you produced into the Drive |
| \`upload_file\` | write | creates a new file (bounded in size) |
| \`create_folder\` | write | the only structural change you may make |
| \`delete_file\` | delete | irreversible |
| \`list_workspaces\` | workspaces.read | the user's cloud workspaces |
| \`get_workspace\` | workspaces.read | runtime state, toolchain, disk budget |
| \`list_workspace_files\` | workspaces.read | files inside a workspace |
| \`read_workspace_file\` | workspaces.read | read one workspace file |
| \`list_artifacts\` | workspaces.read | files recent runs produced |
| \`write_workspace_file\` | workspaces.execute | write a file verbatim |
| \`run_code\` | workspaces.execute | async — returns an execution id |
| \`get_execution\` | workspaces.read | status, exit code, stdout, stderr |
| \`cancel_execution\` | workspaces.execute | stop a run that is taking too long |
| \`save_workspace_environment\` | workspaces.execute | persist installed packages |
| \`create_workspace\` | workspaces.execute | only when the user asks for a new one |

Call \`tools/list\` for current schemas rather than assuming them.

Reading a large file — \`download_file\` returns about 128 KB per call, because a
result travels as a single transport event with a size cap:

1. Call with just \`file_id\` and read the first part plus \`has_more\`.
2. While \`has_more\` is true, call again with \`offset\` set to \`next_offset\`.
3. Concatenate the decoded parts **in order**. One part on its own is not the file.

Never summarise a file you have only partly read: say what you saw and that it
was truncated.

Deliberate limitations — do not work around these:
- Every tool takes a \`file_id\` / \`job_id\` and resolves it server-side. You can
  never name a storage object, and no storage key or presigned URL is ever
  returned.
- \`upload_file\` is the only way to introduce content the user did not already
  have, and it is size-capped. \`save_file\` only promotes a job Transform itself
  produced.
- There is no tool that deletes or renames a folder or a workspace. Deletion of a
  workspace is a decision a human makes in the app.

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
- The user approved but no code arrived — your loopback listener was not
  accepting connections when they confirmed the handoff. The grant already
  exists (they can see it under Settings → AI apps), so have them retry the
  connection instead of approving again.
`;
