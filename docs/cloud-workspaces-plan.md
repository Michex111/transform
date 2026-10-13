# Persistent Python Cloud Workspaces — Audit & Implementation Plan

Status: Phase 1 (audit + design). This document is the reviewable plan that
precedes the implementation. It describes only what was verified in the
repository at commit time; anything unverified is marked *assumed* or *gap*.

---

## 1. Relevant current architecture (verified)

Transform is a Clean-Architecture / DDD Python service with a React SPA.

| Concern | Reality (verified) |
|---|---|
| Backend | FastAPI, `src/presentation/api/main.py`. Routers under `/api/v1/*` + non-versioned `/api/users`, `/api/conversions`, `/api/guest`, `/api/uploads`. |
| Hosting | `transform-api` on **Render free** (Docker, `deployment/docker/worker.Dockerfile`), SPA `transform-web` on a Render static site (`transform.com`). |
| Workers | **Run locally in Docker on the dev host**, consuming shared Upstash Redis, writing to shared Neon Postgres / Backblaze B2. Render free has no worker/background service. |
| Queue | Redis Streams. `JobStream` (producer, unbounded) / `JobStreamConsumer` (consumer groups, PEL, `XAUTOCLAIM` reclaim, dead-letter). Stream names namespaced by `QUEUE_STREAM_PREFIX` via `stream_names.qualify()`. |
| Events | `JobEventPublisher` → `conversion_job_events` (maxlen 100 000); `JobEventSubscriber` is a plain `XREAD` reader (no group/ACK) reused by the SSE router. |
| Storage | MinIO/B2 via `MinioUrlStorageAdapter` (presigned) + `MinioFileStorageAdapter` (streaming). Optional at-rest AES-GCM (`ENCRYPTION_MASTER_KEY`). |
| DB | SQLAlchemy 2.0 async + Alembic. Head revision **`0027_agent_folder_scope`**. Migrations are PG-only, guarded/idempotent, run at boot when `RUN_MIGRATIONS=true`. |
| Auth | `get_current_user`: JWT bearer **or** `X-API-Key`. Ownership enforced at the repository `WHERE` and by `assert_job_owner`. |
| MCP | Official `mcp` SDK v2, OAuth 2.1 + PKCE, per-tool scopes, folder confinement. 11 tools. |
| Frontend | React 18 + Vite 5 + TS + Tailwind **v4** (`@theme` in `src/index.css`, no `tailwind.config.js`), react-router v7. No data-fetching lib, no jsdom (tests use `renderToString` + `vi.mock`). |
| Gates | CI = `uv run pyrefly check` + `uv run pytest -q`; frontend = `npm run lint && npm test && npm run build`. Deploy only after `verify`. |

### 1.1 Reusable components for this feature

- **Job/queue machinery** — the Redis-Streams producer/consumer (reclaim,
  dead-letter, composite ACK routing, SIGTERM handling) is directly reusable for
  executions; only the stream name and message schema are new.
- **Event streaming** — `JobEventSubscriber` + the SSE router pattern
  (`StreamingResponse`, heartbeat, `is_disconnected`) covers execution logs.
- **Worker template** — `workers/cleanup_worker/` is a second, non-queue worker
  bootstrap to copy for a workspace worker.
- **Repository/service/router/schema/migration conventions** — the saved-workflow
  feature (`0026_batch_and_workflows`) is the exact vertical-slice template.
- **`FileService` + object storage** — workspace file import/export can reuse
  presigned URL + streamed download, not a new storage path.
- **`Base(list)` ORM, `get_db_session`, DI factories, `tests/fakes/*`.**

### 1.2 Architectural gaps (do not exist today)

- No execution/sandbox abstraction of any kind; **no managed sandbox provider**
  (verified: no e2b/modal/daytona/fly/firecracker/gvisor/nsjail reference).
- No `docker` client, no `/var/run/docker.sock`, no `DOCKER_HOST`, no image
  registry, no image publish pipeline, no resource-limit convention.
- No lifecycle state machine outside `ConversionJob`; `JobStatus` has no
  `CANCELLED`.
- No generic repository base class and no pagination helper (offset math is
  repeated inline per router).
- Render free web services expose **no volumes and no Docker socket**, so the
  API container cannot create sibling containers.

---

## 2. Runtime provider recommendation

### 2.1 Options considered

| Option | Isolation | Persistence | Verifiable here | Verdict |
|---|---|---|---|---|
| **Docker Engine, single host** (the host the workers already run on) | namespace/cgroup container, non-root, `--network none`, `--pids-limit` | named volumes + `docker commit` snapshots | **Yes** — daemon reachable (Desktop 4.94.0, Compose v5.5.1) | **Recommended for the MVP** |
| E2B / Modal / Daytona / Fly Machines | microVM / managed sandbox (stronger) | provider snapshot (E2B/Fly), volume (Modal) | No credentials, unverifiable, cost unknown | Deferred — same port |
| Kubernetes Jobs | strong | PVC | no cluster in the repo (`deployment/kubernetes/` does not exist) | Rejected (over-engineering) |
| In-process `subprocess` in the API | none | n/a | — | **Rejected** (violates the security requirement) |

### 2.2 Recommendation

Implement a **provider-neutral `RuntimeProviderPort`** and ship one concrete
adapter, **`DockerRuntimeProvider`**, backed by the Docker Engine on the host
that already runs the workers. This is the only execution substrate present in
the repository today, so it adds no competing infrastructure (a hard requirement
of the brief) and no new always-on service.

**This is deliberately the MVP provider, not the end state.** A managed
microVM provider can be added behind the same port without touching the domain,
services, API, or UI. The provider is selected by configuration
(`WORKSPACE_RUNTIME_PROVIDER`, `docker | fake`), never by hardcoding.

### 2.3 Persistence guarantees (what actually survives)

| Event | Workspace files (`/workspace`) | Installed packages | Running processes / in-memory Python state |
|---|---|---|---|
| `stop` (container stopped, volume kept) | **Survives** | Survives only if snapshotted | **Lost** |
| restart from base image | Survives | Restored via `uv sync` from the persisted `uv.lock` | **Lost** |
| restart from a saved snapshot | Survives | **Survives** (image includes `/opt/venv`) | **Lost** |
| runtime crash / `FAILED` | **Survives** | Survives (volume + last good snapshot) | **Lost** |
| container deleted / provider replaced | **Survives** (volume reattached) | Survives via snapshot or `uv sync` | **Lost** |
| application deploy | **Survives** (volume is host state) | Survives | **Lost** |

The UI must state exactly this. Live variables and running processes are **not**
durable and are never implied to be.

**Key design decision that makes snapshots meaningful:** workspace dependencies
are installed into `UV_PROJECT_ENVIRONMENT=/opt/venv` and cached at
`/opt/uv-cache`, both **outside** the `/workspace` volume. `docker commit`
captures the container filesystem but not anonymous/named volume mounts, so
dependency state must live in the image filesystem to be snapshot-able, while
user files must live in the volume to survive container replacement. Without
this split, `docker commit` would capture nothing useful.

Secrets (API keys, tokens) are injected per-execution via `docker exec -e`
(or omitted entirely in the MVP) and are **never** baked into a snapshot.

---

## 3. Proposed changes

### 3.1 Domain model — `src/domain/workspaces/`

| Entity / VO | File | Notes |
|---|---|---|
| `Workspace` | `entities/workspace.py` | id, owner, name, description, `RuntimeConfig`, state, timestamps, quota/usage metadata. Durable logical resource; never a container. |
| `RuntimeConfig` (VO) | `value_object/runtime_config.py` | python_version, uv_version, base_image ref+revision, dependency refs, resource profile, config_version. Emits an **environment fingerprint**. |
| `WorkspaceRuntime` | `entities/workspace_runtime.py` | internal id, workspace id, provider + provider ref, state, image revision, start/stop/last-activity, resource allocation, failure metadata. |
| `Execution` | `entities/execution.py` | id, workspace id, mode (script/command/project-op), source ref, status, timings, exit code, bounded stdout/stderr refs, error, artifacts, resource usage, idempotency key. |
| `RuntimeImageRevision` | `entities/runtime_image_revision.py` | revision id, base revision, provider snapshot ref, workspace association, fingerprint, creation status/time, validation metadata, error. |
| `RuntimeState`, `WorkspaceState`, `ExecutionStatus`, `ExecutionMode`, `ImageRevisionStatus` | `value_object/*.py` | `StrEnum`, uppercase (matches PG enum convention). |

Lifecycle invariants live in the entities (`start()`, `stop()`, `succeed()`, …)
mirroring `ConversionJob`; illegal transitions raise `InvalidStateTransition`.

Runtime: `STOPPED → STARTING → RUNNING → STOPPING → STOPPED`, plus `FAILED`.
Execution: `QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED | TIMED_OUT`.
Image revision: `PENDING → BUILDING → READY | FAILED`.

### 3.2 Application

- `ports/runtime_provider_port.py` — provider-neutral `RuntimeProviderPort`:
  `create_runtime`, `start_runtime`, `stop_runtime`, `inspect_runtime`,
  `execute`, `write_file`, `read_file`, `snapshot`, `delete_runtime`.
- `ports/workspace_repository_port.py`, `ports/execution_repository_port.py`.
- `services/workspace_service.py` — CRUD + runtime lifecycle orchestration,
  idempotent start/stop, snapshot compatibility check, ownership scoping.
- `services/execution_service.py` — submit (durable row → queue), get, list,
  cancel, artifact listing.

### 3.3 Infrastructure

- `adapters/runtime/docker_runtime_provider.py` — the Docker adapter (provider
  IDs stay inside).
- `adapters/runtime/fake_runtime_provider.py` — used by tests and by
  `WORKSPACE_RUNTIME_PROVIDER=fake` for a deployment without a daemon.
- `adapters/repository/sql_workspace_repo.py`, `sql_execution_repo.py`.
- `database/models/workspace.py`, migration **`0028_cloud_workspaces`**
  (head chains from `0027_agent_folder_scope`).
- `deployment/docker/runtime.Dockerfile` — the versioned base image
  (`python:3.14-slim` + pinned `uv`, non-root uid 10001, `WORKDIR /workspace`,
  `UV_PROJECT_ENVIRONMENT=/opt/venv`, `UV_CACHE_DIR=/opt/uv-cache`).

### 3.4 Workers

`workers/workspace_worker/` — a third worker (same template as
`cleanup_worker`): consumes `workspace_executions` (prefix-aware), claims
idempotently, ensures the runtime, executes under resource limits, captures
bounded stdout/stderr, persists status/artifacts, publishes SSE events.

### 3.5 API (new router group, existing conventions)

`POST|GET|PATCH|DELETE /api/v1/workspaces`, `…/{id}/runtime/{start|stop|restart|configuration}`,
`…/{id}/environment[/save]`, `…/{id}/executions`, `…/{id}/artifacts`,
`GET /api/v1/executions/{id}`, `POST /api/v1/executions/{id}/cancel`.

### 3.6 Frontend

`/app/workspaces` (list) + `/app/workspaces/:id` (Overview / Files / Execute /
Executions / Environment / Artifacts). New `NAV` entry, `SPA_ROUTES` stub,
`types.ts` unions, `normalize.ts` guards, `ApiClient` methods (one returning a
cleanup fn for the log stream), `ui.tsx` primitives, real loading/empty/error/
retry states. No code editor exists — a styled textarea is used; the backend is
the source of truth.

### 3.7 MCP / SDK

A new `workspaces.*` tool group (scopes added to `AgentScope`, fail-closed
`_require`, folder confinement where file-scoped). Deferred to Phase 4.

---

## 4. Security & reliability risks

| Risk | Mitigation |
|---|---|
| Arbitrary code execution | Never in the API process. Provider-backed isolation, non-root, `--network none`, `--memory`/`--cpus`/`--pids-limit`, execution timeout, bounded output. |
| Cross-workspace access | Every repo read/write scoped by `user_id`; per-workspace volume; provider names prefixed by workspace id. |
| Secrets in snapshots | No secrets baked; injected per-execution; documented. |
| Snapshot creep / cost | Snapshots only on explicit save or a successful dependency change; fingerprint reuse; cleanup policy in the cleanup worker. |
| Duplicate executions | Durable row + idempotency key; worker claims idempotently; `COMPLETED` short-circuit (mirrors the converter worker guard). |
| Queue/DB non-atomicity | Durable row is the source of truth; the queue message only *signals*. Publish-after-commit, worker re-reads the row. |
| Provider unavailable | `FAILED` runtime state, recoverable path, workspace files untouched; visible in UI. |
| Render cannot host runtimes | Documented: the provider host is the workers' host. The API degrades to a configuration error, never a silent no-op. |
| Zombie containers/volumes | Cleanup worker policy; TTL on abandoned runtimes. |

---

## 5. Implementation sequence

1. **Phase 2a** — domain entities + VOs + state machines + unit tests.
2. **Phase 2b** — ports + `FakeRuntimeProvider` + `DockerRuntimeProvider` +
   `runtime.Dockerfile`; adapter tests against the real daemon where available.
3. **Phase 2c** — DB models + guarded migration `0028` + SQL repos + tests.
4. **Phase 2d** — application services + tests.
5. **Phase 2e** — routers/schemas/DI + integration tests; execution stream +
   `workers/workspace_worker/`.
6. **Phase 3** — frontend slice + tests.
7. **Phase 4** — restart recovery, snapshot reuse, timeouts, cleanup,
   observability, MCP tool group.
8. **Phase 5** — quality gates (`pyrefly`, `pytest`, `npm run lint/test/build`),
   docs, limitations.

Existing file conversion is untouched: no converter, queue stream, or table it
uses is modified.
