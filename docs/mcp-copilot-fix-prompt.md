# MCP server: issue register and fix record

Audit date: 2026-11-13, at commit `c4aec3c`. Scope: `src/presentation/mcp/*`,
`src/application/services/mcp/*`, the OAuth provider and repository, the
`/api/v1/mcp` router, the rate-limit and body-limit middleware, and the web MCP
pages (`web/src/pages/public/McpPage.tsx`, `web/src/lib/mcp*.ts`,
`web/src/components/developer/Mcp*.tsx`).

Severity: **P0** breaks connections or blocks whole capabilities ·
**P1** a security gap or a large capability gap · **P2** correctness or
usability · **P3** cleanup.

> **Status:** the remediation build closed A1–A5, A7, A8, B1–B3, C1–C7, C11,
> D1, D3–D6, D8 and E1–E4. A6, C8–C10 and D2 are deferred with reasons. See
> `docs/mcp-production-audit.md` §12 for the per-ID outcome.

## A. OAuth, tokens and scopes

| # | Sev | Issue |
|---|---|---|
| A1 | P0 | Tool permissions came from the token's frozen scopes, not the grant's current scopes, so editing a connection in Settings had no effect until a full re-authorization. |
| A2 | P0 | A refresh never picked up grant changes, and after a narrowing it looped: `exchange_refresh` reissued the old scopes and `load_access_token` rejected tokens that were not a subset of the grant. |
| A3 | P0 | The consent screen could only grant what the client asked for. A client sending no `scope` resolved to `DEFAULT_SCOPES`, making `workspaces.*` and `documents.delete` impossible to grant. |
| A4 | P0 | Refresh-token rotation had no reuse grace period and was not atomic. A retried/concurrent refresh got `invalid_grant`; check-then-act raced. |
| A5 | P1 | OAuth endpoints shared an anonymous per-IP bucket of 30 req/min. Hosted clients share egress IPs, and uvicorn ran without `--proxy-headers`. |
| A6 | P2 | No step-up authorization (`403` + `insufficient_scope`); the 401 challenge carried no `scope` hint. |
| A7 | P2 | Expired codes and tokens were never purged; `delete_expired` was uncalled. |
| A8 | P3 | `load_refresh_token` logged `unknown-or-revoked` for every case and did not check expiry itself. |

## B. Folder confinement gaps

| # | Sev | Issue |
|---|---|---|
| B1 | P1 | `save_file` let a folder-confined agent copy content in from outside its folder: it checked job ownership/status but not that the source file was in the subtree. |
| B2 | P1 | `get_conversion_status` skipped the subtree check, leaking file names and errors for out-of-folder jobs. |
| B3 | P2 | `get_conversion_history` with `AGENT` included jobs from every MCP connection; job rows had no `grant_id`. |

## C. Capability gaps

| # | Sev | Gap |
|---|---|---|
| C1 | P0 | An agent could not actually read a document: `download_file` returns base64 bytes, which a model cannot reliably decode, and container formats are binary. |
| C2 | P1 | `upload_file` accepted only base64. |
| C3 | P1 | No download link for a Drive file (only for conversion jobs). |
| C4 | P1 | No bridge between the Drive and workspaces. |
| C5 | P1 | The workspace runtime contract contradicted itself: descriptions said "started automatically", the service raised `RuntimeNotFoundError`; start/stop were not exposed. |
| C6 | P2 | Snapshot status could not be checked. |
| C7 | P2 | Output was truncated with no way to page. |
| C8 | P2 | No conversion parameters (the REST endpoint accepts none either). |
| C9 | P2 | No rename/move. |
| C10 | P2 | No MCP resources or prompts. |
| C11 | P3 | `run_code`'s `timeout_seconds=300` default hid `WORKSPACE_EXECUTION_TIMEOUT_SECONDS`. |

## D. Tool contract and correctness

| # | Sev | Issue |
|---|---|---|
| D1 | P2 | `list_files(query=…, folder_id=…)` silently ignored `folder_id`. |
| D2 | P2 | Every tool returns `dict[str, Any]`, so `outputSchema` is untyped and the payload is serialized twice. |
| D3 | P2 | `_tool_scope` raised `RuntimeError` when the grant could not be loaded; the client saw a bare "Error executing tool". |
| D4 | P2 | Document and workspace denial payloads differed; workspace tools wrote no audit entries. |
| D5 | P2 | Telemetry counted expected `ok:false` results as `ERROR`. |
| D6 | P3 | `get_supported_conversions` and `get_credits` went through the folder gate. |
| D7 | P3 | Pagination input handling was inconsistent (`int(x or 0)` vs `_as_int`). |
| D8 | P3 | Stale tool counts in comments. |

## E. Frontend and docs

| # | Sev | Issue |
|---|---|---|
| E1 | P2 | The public MCP page advertised "Seven tools" and four scopes. |
| E2 | P2 | The Connected-apps UI did not say when a permission change takes effect. |
| E3 | P2 | The consent screen could not offer scopes the client did not request. |
| E4 | P3 | `INSTRUCTIONS`, `docs/mcp.md`, `docs/mcp-agent-prompt.md` and `web/src/lib/mcpAgentPrompt.ts` were kept in sync by hand. |

## Non-negotiable invariants

1. Identity comes only from the verified access token.
2. Fail closed: unknown scopes, missing/foreign grants, and a `FOLDER` grant with no folder are refused.
3. "Not found" and "outside your folder" give **byte-identical** responses.
4. Ownership and authorization live in application services, never in presentation code.
5. Expected failures are `{"ok": false, "error": "<sentence>"}` values.
6. `documents.delete` and `workspaces.*` are never pre-selected or implied.
7. No tool lets an agent delete a workspace or a folder.
8. Every tool name is a permanent API contract, pinned with `name=`.
9. Never put an OAuth token in a URL; download links are short-lived capabilities, rechecked on redemption.
