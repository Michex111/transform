# Cloud workspaces — integration, hardening, and completion

This is the audit that preceded the integration work: what existed, what was
disconnected, what was missing, and what cannot be done in this repository.
It is a working document — the *state* below was verified by reading the
repository, not from a summary.

Companion documents: [`cloud-workspaces-plan.md`](./cloud-workspaces-plan.md)
(original design), [`cloud-workspaces.md`](./cloud-workspaces.md) (operator
guide).

---

## 1. Gap matrix

### 1.1 Implemented and verified

| Capability | Evidence |
|---|---|
| Workspace domain (4 state machines, fingerprinted runtime config, closed `uv` operation allow-list) | `src/domain/workspaces/**`; `tests/unit/domain/test_*.py` |
| Runtime lifecycle: idempotent start, config-change retirement, snapshot reuse/compatibility | `src/application/services/workspaces/workspace_service.py`; `tests/unit/application/test_workspace_service.py` |
| Docker provider: path jail, in-container `timeout -s KILL`, tmpfs output capture, cgroup limits, cap-drop, no-new-privileges | `src/infrastructure/adapters/runtime/docker_runtime_provider.py`; `tests/unit/infrastructure/test_docker_runtime_provider.py` |
| Durable executions: typed lifecycle, conditional claim, idempotency key, cancel-wins-over-outcome | `src/application/services/workspaces/execution_service.py`; `tests/unit/workers/test_workspace_processor.py` |
| 22 API operations over 18 paths, owner-scoped, centrally mapped errors | `src/presentation/api/routers/v1/workspaces.py`; `tests/integration/test_workspaces_api.py` (27 tests) |
| Migration `0028`, guarded and idempotent | `tests/unit/infrastructure/test_migration_0028_cloud_workspaces.py` |
| Workspace worker (own consumer, re-queue on reclaim, dead-letter before ACK, SIGTERM) | `workers/workspace_worker/**` |
| Workspace UI: list + detail (runtime controls, execute, history, environment, files) | `web/src/pages/app/Workspaces*.tsx`, `web/src/lib/workspaces.ts` |
| Versioned base image + build script | `deployment/docker/runtime.Dockerfile`, `scripts/docker/build_runtime_image.sh` |

### 1.2 Implemented but not integrated (the core problem)

> **Status after this change: all five rows below are resolved.** The AI has ten
> workspace tools (`assistant_workspace_tools.py`), the turn carries a validated
> workspace (`stream_chat(workspace_id=…)` plus a server-authored prompt line), and
> long-running work is submitted rather than awaited. Artifacts are copied into
> object storage by the worker and retrievable through a presigned URL. The UI
> renders the new artifact kinds as links into the workspace. MCP has eight
> workspace tools behind two new scopes. See §3 for the details and §4 for what
> is still open.

| Gap | Detail |
|---|---|
| **The AI cannot use workspaces at all** | `AssistantToolBox` (`src/application/services/assistant/tools.py`) exposes 13 file/conversion tools. None touches workspaces: `specs()` has no workspace entry and `_dispatch()` has no workspace branch. The runtime is a dashboard-only feature — exactly what the brief forbids. |
| **No workspace context in a turn** | `stream_chat` passes only `user_id`/`tier`/`message`/`conversation_id`/`file_ids`/`context`. There is no way to say "operate on workspace X", and no server-authored line in the prompt telling the model which workspace is active. |
| **Long-running work has no representation** | The tool loop (`for _iteration in range(max_iterations)`) assumes each tool returns promptly. Nothing submits durable work and reports progress across turns. |
| **Artifacts are paths, not objects** | `handle_execution` records `ExecutionArtifact(path=…, size_bytes=…)` with `object_key=None`; the API marks such an artifact `downloadable: false`. The bytes stay in the container. |
| **Frontend renders no workspace activity** | `assistantChat.ts`/`AssistantPage.tsx` render the existing 13 tool labels; there are no labels, artifact kinds or links for workspace tools. |
| **Workspace tools are not in the MCP tool group** | `src/presentation/mcp/tools.py` registers 11 file/conversion tools; `workspace` appears nowhere. |
| **No SDK for workspaces** | There is **no Python SDK and no TypeScript SDK package** in the repository (verified: no `sdk/` directory, no package). The typed endpoint list in `web/src/api/client.ts` is the only client, and it is SPA-internal. |

### 1.3 Missing and required

> **Status: mostly closed.** An enforced egress policy and an enforced disk
> budget are in place (§6.1/§6.2 of the operator doc), artifacts are persisted,
> output is redacted, and secret *injection* is deliberately still absent (there
> is no secret store, and inventing one was forbidden). Cleanup/reconciliation of
> orphaned provider resources remains open. Real-Docker execution is still
> unverified — see §4.

| Gap | Note |
|---|---|
| Enforced network egress policy | Today `WORKSPACE_RUNTIME_NETWORK_DISABLED=false`; the runtime has unrestricted egress and nothing enforces a policy. |
| Enforced disk quota | `ResourceProfile.disk_mb` is recorded and displayed but never applied to anything. |
| Durable artifact storage | Requires copy-out to the existing object storage. |
| Cleanup / reconciliation of orphaned runtimes, volumes and snapshots | Only READY-snapshot pruning exists (in the worker). Failed-delete volumes are leaked. |
| Secret redaction from execution output and stored logs | Nothing scrubs credentials from captured stdout/stderr. No secret-injection mechanism exists (documented as absent). |
| Real-Docker execution test | The provider's daemon interactions are covered only by pure-logic tests. |

### 1.4 Blocked by infrastructure or provider limits

| Item | Precise reason |
|---|---|
| **Production runtime execution** | The Render **free** web service has no Docker socket and no volumes. The runtime is created by the local-Docker workspace worker. There is no way to run this on Render free; it needs a host with a daemon (current arrangement) or a managed sandbox provider. |
| **Per-workspace disk quota via `--storage-opt`** | Docker's `size` storage option requires the daemon to be on **overlay2 over XFS with `pquota`**, or devicemapper. It is not generally available; on a stock Docker Desktop / ext4 host the flag is rejected. This document records what *is* enforced instead. |
| **Genuine egress control** | A Docker container with any network attachment can reach any routable address; `iptables` rules inside the container need `CAP_NET_ADMIN` (which is dropped) and a host-level policy is outside this repository's deployment surface. What can be enforced here is a **default-deny network attachment plus an explicit allow-list at the proxy boundary** — and that requires a proxy the deployment does not have. Documented, not faked. |
| **Managed sandbox provider** | No credentials exist for E2B/Modal/Daytona/Fly. The port is ready for one; the adapter cannot be written blind and claimed to work. |
| **Git tools** | There is **no Git integration anywhere in the repository** (no GitHub App, no token store, no clone support). The brief says to expose Git tools "when the underlying integration is available" — it is not, so **no Git tools are registered**. Inventing them would be exactly the "imaginary tools" the brief forbids. |
| **Live model calls in CI** | `AI_BACKEND=echo` is the CI default; there is no paid-model opt-in suite. AI tests therefore drive the real orchestration with a deterministic provider. |

---

## 2. Architectural decisions taken

**One toolbox, not a second one.** The assistant keeps a single
`AssistantToolBox`; workspace tools live in a collaborator
(`AssistantWorkspaceToolBox`) that it composes. `specs()`, `execute()`,
`label_for()` and `describe()` merge the two sets, so the agent loop, the
streaming events, the artifact accumulation and the persistence path are
unchanged. A parallel tool engine was explicitly not built.

**Workspace selection is context, never authority.** The selected workspace
travels as a per-request parameter and is *also* persisted in the user message's
existing `meta` JSON (no schema change), so a follow-up turn can resolve it. Every
tool call re-validates ownership through `WorkspaceService.require_workspace`,
which returns the same not-found for "missing" and "not yours". A workspace id
recalled from conversation history cannot become an access token.

**Long-running work is submitted, not awaited.** `run_python` submits through
`ExecutionService` (durable row → queue → worker) and waits only a bounded,
short interval for a fast result. Anything longer returns
`{"status": "running", "execution_id": …}`, and the model inspects it with
`get_execution` on a later iteration or the user reopens the workspace. The HTTP
stream is never held open for a build.

**Authorization is enforced server-side, not by the model.** Every workspace tool
calls the same `WorkspaceService`/`ExecutionService` the REST API calls, so the
toolbox has no privileged path: quotas, ownership, the closed `uv` operation
allow-list, path validation and runtime isolation all apply unchanged.
