# Cloud workspaces

Persistent, agent-usable Python environments with `uv`, attached to a durable
workspace. This document is the operator/developer guide: what runs where, what is
durable, how to build and run it, and what is deliberately not finished.

Design rationale and the audit that preceded the implementation are in
[`cloud-workspaces-plan.md`](./cloud-workspaces-plan.md).

---

## 1. The shape of it

| Concept | What it is | Durable? |
|---|---|---|
| **Workspace** | The logical resource a user owns: id, owner, name, configuration, files, execution history. Server-minted id, never a container name. | **Yes** — survives everything |
| **Runtime** | The disposable compute instance currently attached to a workspace. | No — replaced freely |
| **Runtime image revision** | A saved snapshot of a workspace's installed environment. | Yes, until pruned |
| **Execution** | One submitted unit of work, with its output and exit code. | **Yes** — outlives the runtime and the browser |

The rule that everything follows: **the runtime is disposable, the workspace is
not.** No code path deletes a user's files to fix a runtime problem.

### Where each piece runs

```
browser ──► transform-api (Render, Docker, free tier)
                 │  no Docker socket, no volumes
                 │
                 ├─ Postgres  (workspaces, runtimes, executions, revisions)
                 └─ Redis     (workspace_work stream)
                                 ▲
                                 │  consume
                    workspace worker  (LOCAL Docker container, has the socket)
                                 │
                                 └─► Docker Engine ──► one container per runtime
                                                        + one named volume
```

**The API never executes user code and cannot create containers.** The Render free
web container has no Docker socket and no volume support, so a runtime is created
by the workspace worker, which runs where the daemon is. This mirrors the existing
converter-worker topology. A consequence worth stating plainly: **if the workspace
worker is not running, executions stay `QUEUED`** — the API does not pretend the
work happened.

---

## 2. Persistence guarantees

| Event | `/workspace` files, `pyproject.toml`, `uv.lock` | Installed packages | Running processes, in-memory values |
|---|---|---|---|
| `stop`, then `start` | Survive | **Survive** (the same container is restarted; its writable layer holds the venv) | Lost |
| Restart from a **saved snapshot** | Survive | **Survive** (the snapshot is an image of `/opt/venv`) | Lost |
| Runtime **replaced** (deploy, host rebuild, config change) | Survive | Restored from a snapshot, or rebuilt by `uv sync` from `uv.lock` | Lost |
| Runtime **crash / `FAILED`** | Survive | Survive | Lost |
| Workspace **deleted** | **Removed** | Removed | Lost |

The two layout facts that make this work:

* `/workspace` is a **named Docker volume** — user files.
* `/opt/venv` and `/opt/uv-cache` are **inside the container filesystem, outside
  the volume** — the environment. `docker commit` captures the container
  filesystem and *not* volume mounts, so dependency state must live there to be
  snapshot-able, while user files must live in the volume to survive the
  container being replaced. Moving the venv into `/workspace` would make
  environment snapshots capture nothing.

**Never claimed:** that running processes, open files, background jobs or
in-memory Python variables survive a stop. They do not, and the UI says so.

### What a snapshot matches on

A revision is reusable only when **both** its `config_fingerprint` (base image
revision + Python + uv + config version + resource profile) **and** its
`base_image_revision` match the workspace's current configuration. The
`uv.lock` digest is deliberately *not* part of that key: the lockfile lives inside
the workspace, so its digest cannot be read before a runtime exists — and a
runtime is what a start is trying to obtain. The lockfile is reconciled *after*
the runtime is up by `uv sync`, which is idempotent. The digest is recorded in the
revision's `validation_metadata` so drift is visible without being load-bearing.

---

## 3. Runtime image

`deployment/docker/runtime.Dockerfile` — `python:3.14-slim`, pinned
`uv==0.11.23`, non-root uid 10001, `WORKDIR /workspace`, `UV_PROJECT_ENVIRONMENT=/opt/venv`,
`UV_CACHE_DIR=/opt/uv-cache`.

```bash
# Build the revision the domain expects (tag must match base_image_revision).
scripts/docker/build_runtime_image.sh 1.0.0-py3.14

# Or the default tag (1.0.0-py3.14):
scripts/docker/build_runtime_image.sh
```

The tag **must** equal `RuntimeConfig.base_image_revision` in
`src/domain/workspaces/value_object/runtime_config.py`. The revision is stored
with every workspace and compared on every start, so a base-image change is a
deliberate two-part edit: bump the constant, build the tag. A floating `latest`
would defeat the compatibility check entirely.

---

## 4. Running the workspace worker

The worker needs the Docker socket. It is **not** a compose service (the existing
compose topology deliberately does not mount a socket), so it is run explicitly:

```bash
# One image, two tags: the worker image is the API image.
docker build -f deployment/docker/worker.Dockerfile -t transform-app:test .

# The socket is owned by root:<gid> with mode 0660, and the image runs as the
# non-root `appuser` (uid 10001) — which is NOT in that group. Without
# --group-add the docker SDK cannot open the socket and every start fails with
# "The Docker daemon is not reachable from this process.", which reads like a
# missing daemon rather than a permission problem.
SOCK_GID=$(stat -c '%g' /var/run/docker.sock)

docker run -d --name transform-workspace-worker \
  --network docker_converter-net \
  --env-file "$PWD/.env" \
  --group-add "$SOCK_GID" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --restart unless-stopped \
  --stop-timeout 300 \
  transform-app:test \
  uv run python -m workers.workspace_worker.main
```

Notes:

* **`--group-add $(stat -c '%g' /var/run/docker.sock)` is required**, not optional.
  The image runs as non-root `appuser` (uid 10001) and the socket is
  `root:<docker-gid>` mode 0660, so without it the SDK gets `Permission denied` /
  "daemon is not reachable" and every runtime start fails.
* **The socket mount is the security boundary.** A process with the Docker socket
  can control the host's daemon, so this container is trusted infrastructure — it
  is not the sandbox. The sandbox is the runtime container it creates, which has
  no socket, dropped capabilities, `no-new-privileges`, memory/CPU/PID limits and
  a tmpfs for captured output.
* `docker-worker` (the converter worker) and this worker share Redis; they use
  different streams (`conversion_jobs:*` versus `workspace_work`), and both stream
  names are namespaced by `QUEUE_STREAM_PREFIX`, so a dev worker cannot consume
  production work.
* A stale container keeps its **old image**. After a rebuild, recreate the
  container rather than restarting it, and verify with
  `docker inspect <name> --format '{{.Image}}'`.

---

## 5. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WORKSPACE_RUNTIME_PROVIDER` | `docker` | Provider adapter. An unknown value is a **boot error**, never a silent fallback. |
| `WORKSPACE_RUNTIME_BASE_IMAGE` | `transform-python-runtime:1.0.0-py3.14` | The versioned base image. Must match a built tag. |
| `WORKSPACE_RUNTIME_VOLUME_PREFIX` | `transform-ws-state-` | Named-volume namespace. |
| `WORKSPACE_RUNTIME_CONTAINER_PREFIX` | `transform-ws-rt-` | Container namespace. |
| `WORKSPACE_RUNTIME_SNAPSHOT_REPOSITORY` | `transform-ws-env` | Repository for committed snapshots. |
| `WORKSPACE_RUNTIME_NETWORK_DISABLED` | `false` | **Leave false.** `uv add` needs a package index; `true` makes dependency installation impossible. See the egress gap below. |
| `WORKSPACE_RUNTIME_STOP_GRACE_SECONDS` | `30` | Grace period handed to the daemon on stop. |
| `WORKSPACE_EXECUTION_TIMEOUT_SECONDS` | `300` | Server-side ceiling on one execution. |
| `WORKSPACE_MAX_OUTPUT_BYTES` | `1048576` | Per-stream capture limit, enforced inside the container. |
| `WORKSPACE_MAX_PER_USER` | `20` | Workspace cap per account. |
| `WORKSPACE_MAX_ACTIVE_EXECUTIONS` | `5` | Concurrent executions per workspace. |
| `WORKSPACE_SNAPSHOT_RETENTION` | `5` | READY snapshots kept per workspace; older ones are pruned by the worker. |

---

## 6. Security posture

**Enforced**

* User code never runs in the API process.
* One container and one named volume per workspace; no shared writable state.
* Non-root uid 10001, `cap_drop: ALL`, `no-new-privileges`, `--pids-limit`,
  `--memory`, `--cpus`.
* Captured output is written to a size-limited **tmpfs**, so a runaway `print`
  loop cannot fill the user's durable volume. It is capped again on read.
* Execution timeout is enforced *inside* the container (`timeout -s KILL`), so a
  program that ignores SIGTERM is actually killed rather than merely abandoned.
* Every file path is resolved and validated against `/workspace`; absolute paths,
  `..` and `~` are refused, and workspace ids are sanitised before they become
  resource names.
* Project operations are a closed allow-list (`sync`/`lock`/`add`) with arguments
  validated as package specifications — an option-shaped argument (`--index-url …`)
  is refused, so a dependency cannot be silently resolved from another index.
* No secrets are baked into snapshots. The runtime receives no database, storage
  or provider credentials.

**Not enforced — the honest gaps**

1. **A Docker container is not a microVM.** It is strong isolation, not
   adversarial-grade isolation. A kernel-level escape is out of scope, and no
   amount of container configuration changes that. Moving to a managed
   microVM/sandbox provider (behind the same `RuntimeProviderPort`) is the
   remaining structural work for adversarial workloads.
2. **No per-workspace filesystem quota.** See §6.2 for exactly what *is*
   enforced. A single execution can still overshoot the budget before the next
   measurement sees it, and the hard fix needs a filesystem that supports
   project quotas.
3. **The proxy egress mode needs infrastructure that is not provisioned here.**
   The adapter enforces it, but nothing in this repository runs the proxy
   container or creates the internal network, so `proxy` cannot be selected
   until an operator does that (§6.1).
4. **No user-secret injection.** A workspace cannot yet authenticate to a
   private package index or a private repository, because there is no secret
   store in the deployment. Nothing is invented to fill that hole: the runtime
   receives no platform credentials at all, which is the safe default.
5. **Base image pinned by tag, not digest.** `uv` is pinned by version, not
   hash-verified.

---

## 6.1 Network egress — what is enforced

`WORKSPACE_RUNTIME_EGRESS` selects one of three **network topologies**. It is not
a warning flag: each value changes how the container is created, and the
container cannot renegotiate its own networking because it runs with all
capabilities dropped and `no-new-privileges`.

| Policy | What the container gets | Enforced by |
|---|---|---|
| `none` **(default)** | No network at all (`network_mode: none`) | Docker, before the process starts. A process inside cannot bring an interface up or rewrite rules — it has no `CAP_NET_ADMIN`. |
| `proxy` | Attached *only* to an operator-provided `internal: true` network, with `HTTP_PROXY` / `HTTPS_PROXY` / `UV_INDEX_URL` injected | Network topology: an internal Docker network has no route off the host, so a runtime that ignores the proxy variables still cannot reach the internet. The proxy is the only way out. |
| `unrestricted` | The default bridge | Nothing. Opt-in only, and the API logs a warning at boot. **Not safe for untrusted code** — the container can reach any routable address, including cloud metadata endpoints. |

Consequences, stated plainly:

* **Under `none`, dependency installation is impossible.** `uv add` needs a
  package index. Rather than letting the resolver time out inside an isolated
  container, the *service* refuses `PROJECT` operations with an explanation.
  That check is server-side: a model or an API client cannot talk its way past
  it. `SCRIPT` and `COMMAND` executions are unaffected — that separation is the
  whole point of the policy.
* **`proxy` refuses to start if its network is missing.** Falling back to the
  default bridge would silently mean "unrestricted" while the operator believed
  otherwise, so the adapter raises instead. Boot-time validation also rejects a
  `proxy` policy with no network or URL configured.
* **This is not an allow-list implementation.** The proxy is where an allow-list
  would live; this repository does not ship one. Setting up the internal network
  and the proxy container is the operator's step.

Setting up `proxy` (example topology, not run by this repository):

```bash
docker network create --internal transform-egress
# A proxy container on BOTH transform-egress and a normal network, with its own
# destination allow-list, then:
#   WORKSPACE_RUNTIME_EGRESS=proxy
#   WORKSPACE_RUNTIME_PROXY_NETWORK=transform-egress
#   WORKSPACE_RUNTIME_PROXY_URL=http://transform-egress-proxy:3128
```

Trade-offs of the three modes, and why `none` is the default: `none` is the only
mode whose guarantee does not depend on anything running correctly outside this
process, and a workspace whose code does not need the network is the common case
(scripts, tests, analysis). A workspace that must install dependencies is an
explicit, visible decision.

### DNS, redirects and address resolution

Under `none` and `proxy` these are not a policy problem, which is deliberate:
there is no resolver credit to abuse. Under `unrestricted`, a container can reach
`169.254.169.254` (and the equivalent on other clouds) as easily as any other
address, and can be redirected there by a name it resolves itself. That is one of
the reasons `unrestricted` is not the default and is documented as unsafe.

---

## 6.2 Disk quota — what is enforced

`WORKSPACE_DISK_QUOTA_MB` (default 5120) is enforced in two places, both
server-side:

1. **Every write into a workspace** measures current usage first and refuses the
   write that would exceed the budget, before any bytes are placed. The refusal
   carries the usage and the limit, so the user knows how much to delete.
2. **Every execution submission** is refused once the workspace is *already* over
   budget, so code cannot be used to fill the host by looping.

Usage comes from `du -sb /workspace` inside the runtime, which is the only way to
get an accurate number out of a named volume. A stopped runtime reports `0` —
deliberately, because an unmeasurable workspace must not read as full, or a
stopped workspace could never be written to again.

The provider also **requests a container storage limit** (`--storage-opt size=…`)
and records whether the daemon accepted it (`storage_quota_enforced` in the
runtime metadata and in `describe()`). On a stock Docker host the answer is
usually *no*: it needs overlay2 on XFS with `pquota`, or devicemapper.

**What this is not:** it is not a filesystem quota. A single execution can write
past the budget before the post-run measurement sees it (the run is not killed
mid-write). Closing that needs one of:

* a filesystem/volume that supports project quotas (XFS + `pquota`), which makes
  `--storage-opt size=` work and turns this into a hard cap enforced by the
  kernel;
* a managed sandbox provider with per-sandbox disk limits.

Until then the guarantee is: **the API cannot be used to grow a workspace past its
budget, and a full workspace stops accepting new work.** That is a real limit
with a real recovery path (delete files), and it is not the same thing as a
kernel-enforced quota.

---

## 6.3 Artifacts — how a result outlives the runtime

Files a run leaves in the workspace's `outputs/` directory (configurable via
`WORKSPACE_ARTIFACT_DIRECTORY`) are copied into the existing object storage when
the execution finishes, via the same bucket, credentials and adapter the rest of
the product uses. Nothing new is introduced.

* The object key is namespaced by workspace **and execution**, so two runs of the
  same script cannot overwrite each other's outputs.
* Limits: `WORKSPACE_MAX_ARTIFACTS` (20) per execution and
  `WORKSPACE_MAX_ARTIFACT_BYTES` (25 MB) per file. Both are bounded because the
  directory is written by user code.
* Copying is **best-effort**. A bucket outage leaves the artifact recorded with no
  object key — the API reports `downloadable: false` / `stored: false`, so no
  caller is promised bytes that do not exist. A storage failure never fails a run
  that already succeeded.
* Retrieval: `GET /api/v1/executions/{id}/artifacts/download?path=…` checks that
  the caller owns the execution, then mints a short-lived presigned URL.
  Ownership is checked on the execution rather than the artifact, because an
  artifact is not a separate resource with its own permissions.

**Not done:** pruning old artifact objects. They accumulate in the bucket under
`<BASE_TARGET_KEY>/workspace/{workspace_id}/execution/{execution_id}/`. The
cleanup policy for them belongs with the rest of §8.1.

**Also swallowed:** delete-time provider failures. `delete_workspace` removes the
record even if removing the volume failed, so a workspace can always be deleted.
The orphaned volume needs the cleanup policy in §8.1.

---

## 6.4 Secrets and output hygiene

* **The runtime receives no platform credentials.** No database URL, no storage
  key, no provider token. There is nothing to leak because nothing is injected.
* **Captured output is redacted before it is stored or shown.** Execution
  stdout/stderr passes through `redact_secrets` on the way out of the tool layer,
  which scrubs `api_key=…`/`token: …`-shaped lines, `Bearer …`, and the common
  provider key shapes (`sk-…`, `AKIA…`, `ghp_…`, PEM headers). This exists because
  a *user's own* script may print a token it read from a file, and a tool result
  is persisted in the conversation and replayed on every later turn — a leaked
  secret would be permanent and would follow the user across devices.
* **Limits:** output is bounded (4 000 characters for an AI tool result, 8 000 for
  the stored excerpt), and the tail is kept because the end of a failing
  program's output is the part that explains it.
* Redaction is defence in depth, not the primary control — the primary control is
  that the runtime never holds a credential to begin with.

**Not implemented:** in-workspace secret injection (a user-supplied token made
available to their own code). There is no secret store in this deployment, and
inventing one out of a database column or an env file is exactly what the brief
forbids. The supported position today is that a workspace cannot authenticate to
a private index or repository.

---

## 7. API

18 paths (22 operations) under `/api/v1`, owner-scoped, all errors mapped
centrally:

```
POST   /workspaces                             201
GET    /workspaces                             200  (page, page_size)
GET    /workspaces/{id}                        200
PATCH  /workspaces/{id}                        200
DELETE /workspaces/{id}                        204
POST   /workspaces/{id}/archive                200
POST   /workspaces/{id}/reactivate             200
GET    /workspaces/{id}/runtime                200
POST   /workspaces/{id}/runtime/start          200 | 202
POST   /workspaces/{id}/runtime/stop?force=    200
POST   /workspaces/{id}/runtime/restart?force= 200 | 202
GET    /workspaces/{id}/runtime/configuration  200
GET    /workspaces/{id}/environment            200
POST   /workspaces/{id}/environment/save       202
POST   /workspaces/{id}/executions             202
GET    /workspaces/{id}/executions             200
GET    /workspaces/{id}/artifacts              200
GET    /workspaces/{id}/files?path=.           200
GET    /workspaces/{id}/files/content?path=…   200
PUT    /workspaces/{id}/files/content          204
POST   /workspaces/{id}/files/delete           204
GET    /executions/{id}                        200
POST   /executions/{id}/cancel                 200
```

Status-code choices that matter:

* **start/restart answer 200 when nothing changed and 202 when they acted.** A
  client can tell "already running" from "I just started it".
* **stop answers 409 while executions are in flight** unless `force=true`, because
  stopping kills the processes and saying so beats silently destroying work the
  UI is still showing.
* **cancel is idempotent** and answers 200 either way.
* The request schema validates the mode-specific source; the service validates it
  again, so the worker and any future SDK get the same answers.

---

## 8. Not finished

Ordered by value. Each is a real gap, not a nicety.

1. **No cleanup/reconciliation worker for provider resources.** Snapshot retention
   is enforced (`WORKSPACE_SNAPSHOT_RETENTION`), but abandoned volumes, snapshot
   images from failed deletes, and stored artifact objects are not reaped. This
   belongs in `workers/cleanup_worker/`, which already has the interval and the
   retention-window plumbing.
2. **The `proxy` egress mode has no proxy to talk to here.** The adapter enforces
   it (see §6.1) but the internal network and the proxy container are operator
   infrastructure this repository does not provision.
3. **No per-workspace filesystem quota.** §6.2 is the precise guarantee. The hard
   version needs XFS project quotas or a provider that offers them.
4. **Execution progress is polled, not streamed.** The API supports controlled
   polling (the detail page polls every 2s while a run is in flight, bounded at
   150 polls; the AI tool waits ≤2.5 s then reports `running`). SSE for execution
   output is not implemented.
5. **No user-secret injection** (§6.4).
6. **The base image is pinned by tag, not digest**, and `uv` is pinned by version
   but not hash-verified.
7. **`WORKSPACE_RUNTIME_PROVIDER` has one implementation.** The port, the fake
   provider and the factory are in place; a managed provider is the work of one
   adapter.
8. **No standalone Python SDK.** The typed client in `web/src/api/client.ts` is the
   SDK surface today (it covers every workspace operation, including the artifact
   download). A packaged client for external Python agents would be a thin
   wrapper over the same endpoints; the MCP tool group is the agent-facing
   interface that exists and is tested.
9. **No Git tools**, in the AI assistant or in MCP. There is no Git integration
   anywhere in this repository (no GitHub App, no token store, no clone support),
   so the brief's "when the underlying integration is available" condition is not
   met. `tests/unit/presentation/test_mcp_workspace_tools.py` pins their absence
   so the gap cannot be filled by accident with a stub that lies.

---

## 9. Tests

| Area | Where |
|---|---|
| Domain: lifecycle state machines, validation, output bounding | `tests/unit/domain/test_{workspace,workspace_runtime,execution,runtime_environment}.py` |
| Application: runtime start/stop/snapshot rules over **real SQL repos** | `tests/unit/application/test_workspace_service.py` |
| Application: submit, idempotency, cancellation, validation, **policy gates** | `tests/unit/application/test_execution_service.py` |
| Application: **disk-quota enforcement and recovery** | `tests/unit/application/test_workspace_quota.py` |
| Adapter: path resolution and the generated execution script | `tests/unit/infrastructure/test_docker_runtime_provider.py` |
| Adapter: **egress topologies, storage-limit degradation, usage measurement** | `tests/unit/infrastructure/test_runtime_security.py` |
| Migration `0028`: tables, indexes, the two real constraints, idempotency | `tests/unit/infrastructure/test_migration_0028_cloud_workspaces.py` |
| Worker: claim, redelivery, cancel-wins, timeout, snapshots, pruning, **artifact copy-out** | `tests/unit/workers/test_workspace_processor.py` |
| API: 27 end-to-end tests over the real app | `tests/integration/test_workspaces_api.py` |
| **AI integration**: tool catalogue, authorization, context, redaction, and a real orchestration run | `tests/integration/test_assistant_workspace_flow.py` |
| **MCP tools**: scopes, unknown ids, run_code honesty, artifact availability | `tests/unit/presentation/test_mcp_workspace_tools.py` |
| Frontend: state wording and action legality | `web/src/lib/workspaces.test.ts` |
| Frontend: the workspace selector | `web/src/components/assistant/AssistantWorkspaceBar.test.tsx` |

The runtime provider is faked in every test: no Docker daemon is required, so the
suite runs in CI. What that means, stated plainly: **the Docker adapter's daemon
interactions are covered only by unit tests of the values it passes** (network
mode, resource limits, storage options, the generated script) **and by manual
verification on a Docker host.** No test in this repository starts a real
container. Running one execution end to end needs a machine with Docker, and that
is the first thing to do on such a machine:

```bash
scripts/docker/build_runtime_image.sh
# then, per §4, start the workspace worker and run a script from the UI or:
#   POST /api/v1/workspaces/{id}/executions  {"mode": "SCRIPT", "source": "print(1)"}
```

---

## 10. How an agent uses a workspace

Three interfaces reach the same application services and therefore the same
authorization rules: the REST API, the Transform AI assistant's tools, and MCP.
None of them has a privileged path.

### From the Transform AI (the in-app assistant)

The user picks a workspace in the assistant's workspace selector (above the
transcript), then asks for work in plain language. The assistant gets ten tools —
`list_workspaces`, `get_workspace`, `create_workspace`, `list_workspace_files`,
`read_workspace_file`, `write_workspace_file`, `run_python`, `get_execution`,
`cancel_execution`, `save_workspace_environment` — and the server writes a line
into its prompt naming the active workspace, or saying that none is selected.

Two behaviours are worth knowing as a user:

* The assistant **will ask which workspace** rather than choosing one. That is a
  hard rule in the tool layer, not a style preference.
* `run_python` often answers "still running". That is not a failure — the run is
  durable and continues whether or not the browser is open. The assistant calls
  `get_execution` to obtain the real result, and the workspace page shows the same
  execution with its status, output and artifacts.

### From MCP (an external agent)

Two scopes are involved and neither is granted by default:

* `workspaces.read` — list workspaces, read their files, read execution results
  and artifacts.
* `workspaces.execute` — create a workspace, write files, run code.

Neither is implied by the `documents.*` scopes, and `workspaces.read` does not
imply `workspaces.execute`. There is no agent-facing tool that deletes a
workspace, deliberately.

```python
# Provision (MCP): one long-running call is the wrong shape, so the tool
# submits and returns an id.
list_workspaces()                       # {"ok": true, "workspaces": [...]}
get_workspace("ws-1")                   # runtime_state, python_version, disk
write_workspace_file("ws-1", "main.py", "print(6 * 7)\n")
run_code("ws-1", "python main.py")      # {"ok": true, "execution_id": "ex-9"}
get_execution("ex-9")                   # status, exit_code, stdout, artifacts
list_artifacts("ws-1")                  # stored: true|false per file
```

### From the REST API (or a script)

```bash
# 1. create
curl -sX POST $API/api/v1/workspaces -H "Authorization: Bearer $TOKEN" \
     -d '{"name": "Analytics"}'
# 2. submit (the runtime starts itself; no separate start call is required)
curl -sX POST $API/api/v1/workspaces/$WS/executions -H "Authorization: Bearer $TOKEN" \
     -d '{"mode": "SCRIPT", "source": "open(\"outputs/report.txt\",\"w\").write(\"hi\")"}'
# 3. poll — the execution is durable, so this can be minutes later, from another device
curl -s $API/api/v1/executions/$EX -H "Authorization: Bearer $TOKEN"
# 4. fetch the artifact (a short-lived presigned URL)
curl -s "$API/api/v1/executions/$EX/artifacts/download?path=outputs/report.txt" \
     -H "Authorization: Bearer $TOKEN"
```

### Stopping and resuming

Stopping the runtime (from the UI or `POST /workspaces/{id}/runtime/stop`) keeps
the volume and the execution history. Starting again restores the files, and
either reuses the same container (so installed packages are still there) or
restores them from a saved environment snapshot. Runnning Python variables, open
file handles and background processes are **not** restored — the UI says so before
the stop, and the assistant is instructed never to claim otherwise.
