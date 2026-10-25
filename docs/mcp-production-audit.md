# MCP production audit — 2026-10-25

Scope: the remote MCP server exposed by `transform-api` (`/mcp` and its OAuth
surface), its 24 tools, and the production deployment at
`https://transform-api-7b3g.onrender.com`.

Method: repository review, a live local end-to-end client run against the real
application stack, and production evidence from the Render control plane and
access logs. Production was **read-only while the audit ran**; the fixed commit
was deployed afterwards, with the owner's authorization, and verified against the
running service (§10). No production row was modified, and no credential or
secret is reproduced here.

---

## 1. Executive summary

The MCP implementation is architecturally sound and its authorization model held
up under every probe: authentication is enforced, scopes are enforced per tool,
folder confinement is indistinguishable from "not found", refresh-token rotation
rejects replay, and revocation stops the very next call.

The audit nevertheless found and fixed **nine defects** (D1–D9 in §6), two of them
agent-facing or security-relevant:

* **Workspace tools returned a bare `Error executing tool <name>`** for ordinary
  runtime states (no runtime yet, runtime stopped, workspace archived). The
  agent got no reason and no next step, indistinguishable from a server fault.
* **`/mcp` had no request-body cap at all**, and `upload_file` base64-decoded a
  model-supplied string of unbounded length *before* its size check. A single
  request could force an arbitrarily large allocation.
* `download_file` read up to **25 MiB per 128 KiB page** and decrypted the whole
  object every time.
* Every tool call performed **two avoidable database round trips** (eager plan
  lookup and an unconditional `last_used_at` write).
* A missing/foreign grant on a validated token fell back to **whole-Drive**
  defaults instead of failing closed.
* `list_artifacts` flattened an unbounded JSON artifact list.
* Token-grant refusals left **no server-side reason** — which is why the live
  connector failure in §4 could not be diagnosed from production.
* `save_file`'s description promised Drive-root semantics that are wrong for a
  folder-confined grant.

**Production readiness: deployed and verified, with one open client issue.**
The hardened commit `ca98349` is live on both Render services and the deployed
artifact was verified against the running service (§10), including the decisive
check that `/mcp` now refuses an oversized body. The one remaining gap is the
**disconnected Gemini connector** (§4): the server side of the refresh flow is
proven correct, the refusal is now logged with its cause, and reconnecting the
app is an owner action in the browser. Gemini-driven tool invocation is therefore
the only part of the matrix that is still unverified against production.

---

## 2. Architecture and production integration

```
Gemini / any MCP client
        │  Streamable HTTP + OAuth 2.1 (PKCE S256, RFC 8707 audience)
        ▼
https://transform-api-7b3g.onrender.com/mcp
        │
        ├── OAuth AS: /mcp/authorize, /mcp/token, /mcp/register, /mcp/revoke,
        │             RFC 8414 metadata (two derivations), RFC 9728 PRM
        ├── presentation/mcp/tools.py      24 thin tool handlers
        ├── presentation/mcp/dependencies   per-call service scope
        └── application/services/mcp/*      authorization rules + toolboxes
                    │
        FileService · ConversionService · TransferService · Workspace/Execution
                    │
        Postgres · Redis streams · B2 object storage · converters (workers)
```

* `/mcp` is an ASGI mount inside the FastAPI app; the tool handlers call the same
  application services as the REST API.
* Access tokens are opaque, hashed at rest, audience-bound, and resolved through
  the grant row on **every** request (immediate revocation).
* Deployed commit at audit start: `03da842` (`dep-dbdc7a0ae00c73fm0c4g`), identical
  to local `HEAD` then; no uncommitted MCP source was live.

### Discovery and authentication probes (production)

| Probe | Result |
|---|---|
| `GET /health`, `/ready` | 200 (0.27 s / 1.41 s) |
| `GET /.well-known/oauth-protected-resource/mcp` | 200, resource `…/mcp`, 7 scopes |
| `GET /.well-known/oauth-authorization-server/mcp` | 200, PKCE S256, code+refresh |
| `POST /mcp` without a token | 307 → `/mcp/` → **401** with `WWW-Authenticate: Bearer … resource_metadata=…` |
| `POST /mcp/token` (Gemini refresh) | **400** — see §4 |

---

## 3. Tool inventory and test status

All 24 advertised tools were exercised through a real MCP SDK client against a
live API process (real Postgres, Redis and B2; the dev worker consumed the dev
stream). Statuses: **P** passed, **P\*** passed with a limitation, **B** blocked.

| Tool | Scope | R/W | Status | Evidence / limitation |
|---|---|---|---|---|
| `get_supported_conversions` | read | R | **P** | full map, per-source, unknown source |
| `list_files` | read | R | **P** | empty drive, folder filter, paging, confined subtree |
| `get_file` | read | R | **P** | owned file; foreign/missing identical |
| `download_file` | read | R | **P** | round-trip bytes, 4-byte page, past-end, confined denial |
| `list_folders` | read | R | **P** | root, inside folder, confined default |
| `create_folder` | write | W | **P** | created; blank name; duplicate |
| `get_conversion_status` | read | R | **P** | live PENDING/PROCESSING job; unknown id |
| `get_conversion_history` | read | R | **P** | ALL scope; empty history |
| `get_credits` | read | R | **P** | balance + reset |
| `convert_file` | convert | W | **P** | unsupported pair; job accepted; completed by worker |
| `save_file` | write | W | **P** | refused while PROCESSING; succeeded after completion (real PDF in B2) |
| `upload_file` | write | W | **P** | text round-trip; invalid base64; empty; no extension; oversized |
| `delete_file` | delete | W | **P** | deleted twice (second = not found) |
| `list_workspaces` | workspaces.read | R | **P** | empty and non-empty |
| `get_workspace` | workspaces.read | R | **P** | detail; unknown id |
| `create_workspace` | workspaces.execute | W | **P** | created without starting compute |
| `list_workspace_files` | workspaces.read | R | **P\*** | unknown id + no-runtime handled; real listing needs Docker |
| `read_workspace_file` | workspaces.read | R | **P\*** | no-runtime handled; real read needs Docker |
| `write_workspace_file` | workspaces.execute | W | **P\*** | no-runtime handled; real write needs Docker |
| `run_code` | workspaces.execute | W | **P\*** | queued + execution id; actual run needs Docker |
| `get_execution` | workspaces.read | R | **P** | unknown id |
| `list_artifacts` | workspaces.read | R | **P** | empty, bounded shape |
| `cancel_execution` | workspaces.execute | W | **P** | unknown id |
| `save_workspace_environment` | workspaces.execute | W | **P\*** | stopped runtime handled; real snapshot needs Docker |

Additional authorization checks (all passed): a read-only grant was refused
`convert`, `delete`, `upload`, `create_folder` and both workspace capabilities
with `required_scope`, before any data was touched; re-consenting the same client
to one folder took effect on the next call with no new token; a file outside the
confinement returned byte-identical answers to a nonexistent one for `get_file`
and `download_file`.

**Gemini-side status: blocked.** The connector is disconnected (§4), so no tool
could be invoked through Gemini during this audit. A local MCP SDK client is a
real MCP client and covered the protocol, but it is not a substitute for the
deployed third-party client; this is called out as unfinished verification.

---

## 4. Production evidence: the Gemini connector refusal

Gemini's custom app is configured with `https://transform-api-7b3g.onrender.com/mcp`
and shows **Last synced 10/21/2026, 4:05 PM**; at audit time its screen offered
**Connect**, i.e. it is disconnected.

The production access log shows the sequence:

| Time (UTC) | Request | Result |
|---|---|---|
| 2026-10-21 19:05–19:07 | many `POST /mcp/` | 200/202 — a working session (matches "last synced") |
| 2026-10-22 10:44:24 | `POST /mcp/token` | 200 — a successful token exchange/refresh |
| 2026-10-25 08:19:54 | `POST /mcp/token` | **400** |
| 2026-10-25 08:19:55 | `POST /mcp/` ×2 | 401 — the client gave up and dropped the connection |

The 400 body was not recorded anywhere: the SDK answers the client directly and
the provider only saw the request when it succeeded. That is defect **D1**.

What the audit *can* exclude: the server's refresh path itself is correct.
Against a live server, the same client performed `refresh → 200`,
replayed the rotated token `→ 400 invalid_grant`, and refreshed the new token
`→ 200`. So the refusal is a state or client-compatibility issue (unknown,
already-rotated, expired or revoked token, or a client-id mismatch), not a
broken rotation. The response body — now logged — is required to decide which.

---

## 5. Performance baseline and post-fix measurements

Environment for the live run: local API process, development Neon Postgres in
`us-east-1`, Upstash Redis, B2 storage, MCP client on the same host. A single
database round trip measured **305–311 ms** from this host, so database round
trips dominate tool latency here; the absolute numbers are not production
expectations.

### Server-side layers (medians of 5)

| Path | Before | After | Change |
|---|---|---|---|
| `load_access_token()` | 550 ms | 422 ms | one fewer write (D5) |
| `open_mcp_scope()` (session + plan + grant) | 429 ms | 305 ms | plan query removed (D5) |
| `open_mcp_scope + list_all_files` | 666 ms | 540 ms | ~126 ms per read tool |
| one DB round trip (`select 1`) | 311 ms | 305 ms | control |

### End-to-end tool medians (same host, same client, n as shown)

| Tool | Before | After | Change |
|---|---|---|---|
| `get_supported_conversions` | 1387 ms (n=3) | 806 ms (n=3) | −42 % |
| `list_files` | 1374 ms (n=8) | 1147 ms (n=8) | −16 % |
| `get_file` | 1172 ms (n=5) | 925 ms (n=5) | −21 % |
| `download_file` | 1217 ms (n=5) | 945 ms (n=5) | −22 % |
| `upload_file` | 1145 ms (n=6) | 796 ms (n=6) | −30 % |
| `create_folder` | 1166 ms (n=4) | 800 ms (n=4) | −31 % |
| `delete_file` | 1257 ms (n=4) | 1033 ms (n=4) | −18 % |
| `save_file` | 1987 ms (n=2) | 903 ms (n=2) | see note¹ |
| all tools, median of medians | — | — | ≈ −22 % |

¹ The before sample included a job that had completed; the after sample was
refused while `PROCESSING`. Not a like-for-like comparison.

The win is the removal of two statements per call; the smaller change in
`list_files` (which performs a real query) is consistent with that.

### `download_file` read volume

| File size | Before, per page | After, per page |
|---|---|---|
| 128 KiB page of a 20 MB file | up to 25 MiB read + full decrypt | 128 KiB + 1 byte, ≤ 1.1 MiB ciphertext read for encrypted objects |

The per-call storage transfer is now bounded by the page, not the file. Total
paging cost is still linear in offset (the reader decrypts a prefix), so a
**range read starting at a chunk boundary** is the next step if bulk paging
becomes a real workload.

### Request-body cap

`POST /mcp/` declaring 42,000,000 bytes → **413 in 3.7 ms**, refused before the
body was read (`/api/` behaviour unchanged).

---

## 6. Defect register

| ID | Severity | Finding | Root cause | Status |
|---|---|---|---|---|
| D1 | High | A refused token grant leaves no server-side reason; a production 400 is undiagnosable | The SDK renders the error; the provider only logs on success | **Fixed** — `invalid_grant` refusals log the error code, description and a 12-char token fingerprint; unknown/revoked refresh tokens log at INFO. Unit-tested |
| D2 | High | Workspace tools answered a bare `Error executing tool X` for expected runtime states | `failures_as_values` caught only 4 of 10 application exceptions | **Fixed** — all expected states map to `{ok:false, error:<sentence>}`; live-verified before/after |
| D3 | High | `/mcp` had no request-body cap; `upload_file` decoded unbounded base64 before its size check | Body middleware was scoped to `/api/`; the decode preceded the check | **Fixed** — `MCP_MAX_REQUEST_BODY_BYTES` (40 MiB), 413 on `Content-Length`; base64 length pre-check; both tested and live-verified |
| D4 | Medium | `download_file` read up to 25 MiB and decrypted the whole object for every 128 KiB page | The toolbox passed the global cap as the read bound | **Fixed** — page+1 byte reads; chunk-aligned prefix decryption; tests at every chunk boundary |
| D5 | Medium | Two unnecessary DB round trips per tool call | Eager plan lookup; unconditional `touch_grant` | **Fixed** — lazy tier resolver (used only by `convert_file`); `last_used_at` checked in memory first. Measured |
| D6 | Medium | A token whose grant could not be loaded fell back to `ALL` (whole Drive) | Defaults were fail-open | **Fixed** — `binding_from_grant` refuses missing/foreign/mismatched grants; unit-tested |
| D7 | Low | `list_artifacts` flattened an unbounded JSON artifact list | Re-implemented the service's loop without its ceilings | **Fixed** — uses `ExecutionService.list_artifacts` (scan + count caps) |
| D8 | Low | `save_file` said an omitted folder means Drive root | Description was written before confinement | **Fixed** — now states bound-folder semantics |
| D9 | Low | `docs/mcp.md` omitted the transport cap | Documentation drift | **Fixed** — configuration table + paging note |
| D10 | Low | Benign `ok:false` outcomes are counted as `ERROR` in MCP Activity | `_classify` treats any non-scope `ok:false` as a fault | **Open** — distorts the error rate; needs a new outcome value and a dashboard change |
| D11 | Medium | Gemini connector is disconnected after a 400 refresh | Unproven (state or client behaviour); server rotation verified correct | **Blocked** — now diagnosable; needs owner reconnection |
| D12 | Low | `README` says `transform-web.onrender.com` still serves the SPA; it returns 404 | Render subdomain policy is disabled | **Open** — docs/config inconsistency, outside MCP |

---

## 7. Security findings

* **Authentication cannot be bypassed.** Anonymous `/mcp` is 401 with a correct
  challenge; a token for another audience is refused; `_tool_scope` refuses to
  run without a verified subject.
* **Authorization is server-side and per tool.** A read-only grant was refused
  every write/execute tool with `required_scope`, before any service call.
* **No cross-tenant access.** Foreign file/job/workspace/execution ids return
  the same answer as missing ones (tested for files, jobs, workspaces,
  executions).
* **Folder confinement is not an oracle.** Outside-folder and nonexistent
  responses are byte-identical for `get_file` and `download_file`.
* **Revocation is immediate.** After `DELETE /connected-apps/{id}` the next call
  failed at the transport (401); the token is checked against the grant row on
  every request.
* **Refresh rotation works.** Replay of a rotated token is `invalid_grant`.
* **No secret leaks in tool payloads.** No storage key, bucket, presigned URL or
  token appears in any tool result; error payloads are sentences, not exception
  reprs. The one place an exception message is interpolated (`save_file` /
  `upload_file` "could not be saved: {exc}") carries application-level,
  user-facing messages from `FileSystemError`; the generic branch says only that
  storage was unavailable.
* **Fixed in this audit:** unbounded MCP request bodies (D3), unbounded base64
  decode (D3), fail-open grant binding (D6).

---

## 8. Code changes and tests

Changed (MCP audit):

```
docs/mcp.md
src/application/services/mcp/access_service.py
src/application/services/mcp/toolbox.py
src/application/services/mcp/workspace_tools.py
src/infrastructure/adapters/security/encryption.py
src/infrastructure/adapters/security/mcp_oauth_provider.py
src/infrastructure/adapters/storage/decrypting_object_reader.py
src/infrastructure/config/settings.py
src/presentation/api/middleware/body_limit.py
src/presentation/mcp/dependencies.py
src/presentation/mcp/tools.py
tests/fakes/fake_mcp_repo.py
tests/unit/application/test_mcp_access_service.py
tests/unit/application/test_mcp_toolbox.py
tests/unit/infrastructure/test_mcp_download_decryption.py
tests/unit/infrastructure/test_mcp_oauth_provider.py        (new)
tests/unit/infrastructure/test_settings_validation.py
tests/unit/presentation/test_body_limit.py
tests/unit/presentation/test_mcp_dependencies.py            (new)
tests/unit/presentation/test_mcp_workspace_tools.py
```

Not touched: the pre-existing local edits in
`src/domain/workspaces/{entities/workspace_runtime.py,value_object/project_operation.py}`.

New regression coverage: expected workspace states as values (6 parametrised
cases), fail-closed grant binding (4), bounded download read, stale-row
termination, base64 pre-check before decode, lazy tier resolution, touch-window
behaviour, MCP body cap, prefix decryption at every chunk boundary, and
refused-grant logging without credential leakage.

No migration, no configuration change is required to deploy; the new setting has
a safe default.

---

## 9. Commands and results

```text
# baseline (before changes)
.venv/bin/python -m pytest -q tests/unit tests/integration -rf
2461 passed, 43 warnings in 963.94s (0:16:03)          exit 0

# targeted after changes
.venv/bin/python -m pytest -q \
  tests/unit/application/test_mcp_toolbox.py \
  tests/unit/presentation/test_mcp_workspace_tools.py \
  tests/unit/presentation/test_body_limit.py \
  tests/unit/infrastructure/test_mcp_download_decryption.py \
  tests/unit/application/test_mcp_access_service.py \
  tests/unit/presentation/test_mcp_dependencies.py \
  tests/unit/infrastructure/test_settings_validation.py
226 passed                                              exit 0

.venv/bin/python -m pytest -q \
  tests/unit/infrastructure/test_mcp_oauth_provider.py \
  tests/unit/infrastructure/test_settings_validation.py
75 passed                                               exit 0

# type check
pyrefly check <changed paths>                           0 errors

# full suite after changes
.venv/bin/python -m pytest -q tests/unit tests/integration -rf
2489 passed, 43 warnings in 978.47s (0:16:18)          exit 0
```

(28 more tests than the baseline: the regression tests added by this audit.
`tests/unit/infrastructure/test_mcp_oauth_provider.py` was added after the full
run started and was executed separately: 75 passed together with the settings
tests.)

Live harness: `/tmp/mcp_audit_{bootstrap,run}.py` (DCR → PKCE → consent API →
token → `mcp` SDK `ClientSession`), 64 recorded calls, output in
`/tmp/mcp_audit_results.json` (before) and after the fixes.

Test resources were removed afterwards: 4 test users, their grants/tokens/
clients, 2 workspaces and executions, files/folders, and the one stored object
were deleted from the development database and bucket.

---

## 10. Deployment and post-deployment verification

**Deployed and verified.** Commit `ca98349` was pushed to `main` (fast-forward
from `03da842`, owner-authorized) on 2026-10-25 at 09:11Z.

| Stage | Evidence |
|---|---|
| Push | `03da842..ca98349  HEAD -> main` (GitHub logged "Bypassed rule violations", the documented owner path) |
| Workflow | `Deploy` run **40179583814** — 4/4 jobs success on `ca98349`: `verify / Backend (pytest)`, `verify / Frontend (lint, test, build)`, `Deploy API`, `Deploy SPA` |
| API build | Render `dep-dbesila9lscs7380bfng`, trigger `deploy_hook`, commit `ca983498`, **live** 09:20:37Z (2m23s) |
| SPA build | Render `dep-dbesili9lscs7380bglg`, commit `ca983498`, **live** 09:18:47Z (33s) |
| Boot | 09:20:19 server start; `MCP transport: enabled at https://transform-api-7b3g.onrender.com/mcp`; no migration or traceback lines |

The deploy hook is not proof of a live build (§9 of the project's own traps), so
every check below was made against the running service **after** Render reported
`live`.

### Post-deployment checks (production)

| Check | Result |
|---|---|
| `GET /health`, `/ready` | 200 / 200, `{"status":"ready","database":"ok"}` |
| `POST /mcp/` anonymous | **401** + `WWW-Authenticate: Bearer … resource_metadata=…` |
| **`POST /mcp/` with 42 MB body** | **413** `REQUEST_TOO_LARGE` — the decisive artifact proof: `/mcp` had *no* cap before this commit |
| `POST /mcp/` with 39 MB body | **401**, not 413 — the cap is 40 MiB, not lower |
| `POST /api/users/token` with 5 MB | 413 (the pre-existing API cap, unchanged) |
| `/api/v1/mcp/{authorize,folder-access,connected-apps}` | 401 each (present and auth-gated) |
| Protected-resource metadata | resource `…/mcp`, all 7 scopes |
| OpenAPI | 119 paths (unchanged by this commit — the workspace routes came with `03da842`; the diff adds no route) |
| SPA (`transform-to.com`, `/app/authorize`, `/mcp`) | 200 / 200 / 200 |
| API access log | `POST /mcp/ 413 Content Too Large` recorded; no errors after the 401s |

Because the 413 is produced by code added in this commit, its presence proves the
running container is `ca98349`; every other fix in that image (workspace error
contract, bounded download/decrypt, lazy tier, fail-closed binding) ships with it.

### What this deployment does *not* prove

* **No authenticated MCP tool call was made against production.** Minting an
  OAuth grant requires the account owner's browser session, and after the
  connector disconnect (§4) there was no live client to drive one. The 24-tool
  matrix in §3 was executed against a live server running this same commit on the
  development stack.
* **Gemini remains disconnected** (§11). The new logging is live now, so the next
  refresh refusal will be explained in the API log.


---

## 11. Unresolved, blocked, and next steps

1. **D11 — Gemini connector (the only blocking item).** Owner action: open the
   custom app, click **Connect**, sign in to Transform and approve. The refusal
   diagnostic is now live, so if the reconnect fails the API log will name the
   cause (`MCP token grant refused: error=… description=…`, or
   `MCP refresh token refused … reason=…`). Until then Gemini tool invocation is
   the one part of the matrix unverified against production.
2. **Production tool invocation** was not performed: no production OAuth grant
   could be minted without the account owner's browser session (the same session
   the disconnected connector needs). Every tool was exercised against a live
   server running this exact commit on the development stack, and the deployed
   container is proven to be this commit by the `/mcp` body-cap behaviour (§10).
3. **Workspace execution** is unverified end to end because Docker is not
   available in this environment. The tool contract, scope enforcement and
   graceful no-runtime behaviour are verified; real file I/O and code execution
   remain dependent on a runtime host.
4. **D10 — telemetry semantics.** Benign refusals inflate the MCP Activity error
   rate. Fix requires a new outcome value and a dashboard update.
5. **D12 — SPA host documentation.** `transform-web.onrender.com` 404s because
   the Render subdomain is disabled; `README` still says it serves the app.
6. **Range reads for `download_file`.** Paging is now bounded per call, but total
   work is still linear in offset. A chunk-aligned S3 range read would make a
   page O(1); design requires a range capability on the storage port.
