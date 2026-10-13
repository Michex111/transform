/**
 * Runtime shape guards for the workspace API.
 *
 * Same contract as `normalize.ts`: every endpoint that returns an array or a
 * nested object the UI dereferences goes through one of these. A malformed
 * response degrades to a defensible default instead of throwing
 * `Cannot read properties of undefined` inside a render — which is the failure
 * mode that makes a backend change look like a blank page.
 *
 * Lossless for well-formed payloads.
 */

import type {
  EnvironmentView,
  Execution,
  ExecutionList,
  ImageRevision,
  ResourceProfile,
  RuntimeConfig,
  RuntimeConfiguration,
  RuntimeSummary,
  Workspace,
  WorkspaceArtifact,
  WorkspaceFileContent,
  WorkspaceFileEntry,
  WorkspaceFileList,
  WorkspaceList,
} from "./workspaceTypes";

type Raw = Record<string, unknown>;

function asRecord(value: unknown): Raw {
  return value && typeof value === "object" ? (value as Raw) : {};
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function normalizeRuntimeConfig(value: unknown): RuntimeConfig {
  const raw = asRecord(value);
  return {
    python_version: asString(raw.python_version, "3.14"),
    uv_version: asString(raw.uv_version),
    base_image: asString(raw.base_image),
    base_image_revision: asString(raw.base_image_revision),
    resource_profile: asString(raw.resource_profile, "standard"),
    config_version: asNumber(raw.config_version, 1),
    image_ref: asString(raw.image_ref),
  };
}

export function normalizeResourceProfile(value: unknown): ResourceProfile {
  const raw = asRecord(value);
  return {
    name: asString(raw.name, "standard"),
    cpu_cores: asNumber(raw.cpu_cores, 0),
    memory_mb: asNumber(raw.memory_mb, 0),
    pids_limit: asNumber(raw.pids_limit, 0),
    disk_mb: asNumber(raw.disk_mb, 0),
  };
}

export function normalizeRuntimeSummary(value: unknown): RuntimeSummary | null {
  if (!value || typeof value !== "object") return null;
  const raw = asRecord(value);
  return {
    runtime_id: asString(raw.runtime_id),
    provider: asString(raw.provider),
    // Defaults to STOPPED: claiming a runtime is RUNNING when the payload did
    // not say so would enable controls the server would reject.
    state: (asString(raw.state, "STOPPED") as RuntimeSummary["state"]),
    resource_profile: asString(raw.resource_profile, "standard"),
    image_revision_id: asNullableString(raw.image_revision_id),
    failure_reason: asNullableString(raw.failure_reason),
    started_at: asNullableString(raw.started_at),
    stopped_at: asNullableString(raw.stopped_at),
    last_activity_at: asNullableString(raw.last_activity_at),
  };
}

export function normalizeWorkspace(value: unknown): Workspace {
  const raw = asRecord(value);
  return {
    workspace_id: asString(raw.workspace_id),
    name: asString(raw.name),
    description: asString(raw.description),
    state: asString(raw.state, "ACTIVE") as Workspace["state"],
    runtime_config: normalizeRuntimeConfig(raw.runtime_config),
    execution_count: asNumber(raw.execution_count),
    created_at: asNullableString(raw.created_at),
    updated_at: asNullableString(raw.updated_at),
    last_activity_at: asNullableString(raw.last_activity_at),
    runtime: normalizeRuntimeSummary(raw.runtime),
    active_executions: asNumber(raw.active_executions),
  };
}

export function normalizeWorkspaceList(value: unknown): WorkspaceList {
  const raw = asRecord(value);
  const workspaces = asArray(raw.workspaces).map(normalizeWorkspace);
  return {
    workspaces,
    total: asNumber(raw.total, workspaces.length),
    page: asNumber(raw.page, 1),
    page_size: asNumber(raw.page_size, workspaces.length),
  };
}

export function normalizeImageRevision(value: unknown): ImageRevision {
  const raw = asRecord(value);
  return {
    revision_id: asString(raw.revision_id),
    status: asString(raw.status, "PENDING") as ImageRevision["status"],
    provider: asString(raw.provider),
    config_fingerprint: asString(raw.config_fingerprint),
    base_image_revision: asString(raw.base_image_revision),
    provider_snapshot_ref: asNullableString(raw.provider_snapshot_ref),
    error: asNullableString(raw.error),
    created_at: asNullableString(raw.created_at),
    ready_at: asNullableString(raw.ready_at),
  };
}

export function normalizeRuntimeConfiguration(value: unknown): RuntimeConfiguration {
  const raw = asRecord(value);
  return {
    config: normalizeRuntimeConfig(raw.config),
    profile: normalizeResourceProfile(raw.profile),
    config_fingerprint: asString(raw.config_fingerprint),
    reusable_revision_id: asNullableString(raw.reusable_revision_id),
    supported_python_versions: asArray(raw.supported_python_versions).map((item) =>
      asString(item),
    ),
    supported_resource_profiles: asArray(raw.supported_resource_profiles).map((item) =>
      asString(item),
    ),
    provider: asRecord(raw.provider),
  };
}

export function normalizeEnvironment(value: unknown): EnvironmentView {  const raw = asRecord(value);
  return {
    config: normalizeRuntimeConfig(raw.config),
    profile: normalizeResourceProfile(raw.profile),
    config_fingerprint: asString(raw.config_fingerprint),
    reusable_revision_id: asNullableString(raw.reusable_revision_id),
    revisions: asArray(raw.revisions).map(normalizeImageRevision),
    persistence_note: asString(raw.persistence_note),
  };
}

export function normalizeExecution(value: unknown): Execution {
  const raw = asRecord(value);
  return {
    execution_id: asString(raw.execution_id),
    workspace_id: asString(raw.workspace_id),
    mode: asString(raw.mode, "SCRIPT") as Execution["mode"],
    status: asString(raw.status, "QUEUED") as Execution["status"],
    source: asString(raw.source),
    runtime_id: asNullableString(raw.runtime_id),
    exit_code: typeof raw.exit_code === "number" ? raw.exit_code : null,
    stdout: asString(raw.stdout),
    stderr: asString(raw.stderr),
    stdout_truncated: asBoolean(raw.stdout_truncated),
    stderr_truncated: asBoolean(raw.stderr_truncated),
    error: asNullableString(raw.error),
    artifacts: asArray(raw.artifacts).map((item) => {
      const entry = asRecord(item);
      return {
        path: asString(entry.path),
        size_bytes: asNumber(entry.size_bytes),
        content_type: asString(entry.content_type, "application/octet-stream"),
        downloadable: asBoolean(entry.downloadable),
      };
    }),
    duration_ms: asNumber(raw.duration_ms),
    memory_peak_bytes: asNumber(raw.memory_peak_bytes),
    created_at: asNullableString(raw.created_at),
    started_at: asNullableString(raw.started_at),
    completed_at: asNullableString(raw.completed_at),
    settled: asBoolean(raw.settled),
  };
}

export function normalizeExecutionList(value: unknown): ExecutionList {
  const raw = asRecord(value);
  const executions = asArray(raw.executions).map(normalizeExecution);
  return {
    executions,
    total: asNumber(raw.total, executions.length),
    page: asNumber(raw.page, 1),
    page_size: asNumber(raw.page_size, executions.length),
  };
}

export function normalizeWorkspaceArtifacts(value: unknown): WorkspaceArtifact[] {
  const raw = asRecord(value);
  return asArray(raw.artifacts).map((item) => {
    const entry = asRecord(item);
    return {
      execution_id: asString(entry.execution_id),
      path: asString(entry.path),
      size_bytes: asNumber(entry.size_bytes),
      content_type: asString(entry.content_type, "application/octet-stream"),
      downloadable: asBoolean(entry.downloadable),
    };
  });
}

export function normalizeWorkspaceFileEntry(value: unknown): WorkspaceFileEntry {
  const raw = asRecord(value);
  return {
    path: asString(raw.path),
    name: asString(raw.name),
    is_dir: asBoolean(raw.is_dir),
    size_bytes: asNumber(raw.size_bytes),
  };
}

export function normalizeWorkspaceFileList(value: unknown): WorkspaceFileList {
  const raw = asRecord(value);
  return {
    path: asString(raw.path, "."),
    entries: asArray(raw.entries).map(normalizeWorkspaceFileEntry),
  };
}

export function normalizeWorkspaceFileContent(value: unknown): WorkspaceFileContent {
  const raw = asRecord(value);
  return {
    path: asString(raw.path),
    size_bytes: asNumber(raw.size_bytes),
    content: asString(raw.content),
    // Anything that is not explicitly base64 is treated as text: guessing
    // base64 would render a binary blob as mojibake in a text view.
    encoding: raw.encoding === "base64" ? "base64" : "utf-8",
    truncated: asBoolean(raw.truncated),
  };
}
