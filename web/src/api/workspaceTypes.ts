/**
 * Types for the cloud-workspace API.
 *
 * A self-contained surface gets its own module (the same rule the developer
 * observability types follow) so `types.ts` does not become the single place
 * every feature has to be edited.
 *
 * Lifecycle values are string-literal unions rather than TS enums, matching the
 * rest of this codebase. They are the *server's* vocabulary: the UI renders
 * `STARTING` because the server said so, never because it inferred it.
 */

export type RuntimeState =
  | "STOPPED"
  | "STARTING"
  | "RUNNING"
  | "STOPPING"
  | "FAILED";

export type WorkspaceState = "ACTIVE" | "ARCHIVED" | "DELETING";

export type ExecutionMode = "SCRIPT" | "COMMAND" | "PROJECT";

export type ExecutionStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED"
  | "TIMED_OUT";

export type ImageRevisionStatus = "PENDING" | "BUILDING" | "READY" | "FAILED";

export interface RuntimeConfig {
  python_version: string;
  uv_version: string;
  base_image: string;
  base_image_revision: string;
  resource_profile: string;
  config_version: number;
  image_ref: string;
}

export interface ResourceProfile {
  name: string;
  cpu_cores: number;
  memory_mb: number;
  pids_limit: number;
  disk_mb: number;
}

export interface RuntimeSummary {
  runtime_id: string;
  provider: string;
  state: RuntimeState;
  resource_profile: string;
  image_revision_id?: string | null;
  failure_reason?: string | null;
  started_at?: string | null;
  stopped_at?: string | null;
  last_activity_at?: string | null;
}

export interface Workspace {
  workspace_id: string;
  name: string;
  description: string;
  state: WorkspaceState;
  runtime_config: RuntimeConfig;
  execution_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  last_activity_at?: string | null;
  runtime?: RuntimeSummary | null;
  active_executions: number;
}

export interface WorkspaceList {
  workspaces: Workspace[];
  total: number;
  page: number;
  page_size: number;
}

export interface RuntimeConfiguration {
  config: RuntimeConfig;
  profile: ResourceProfile;
  config_fingerprint: string;
  reusable_revision_id?: string | null;
  supported_python_versions: string[];
  supported_resource_profiles: string[];
  provider: Record<string, unknown>;
}

export interface ImageRevision {
  revision_id: string;
  status: ImageRevisionStatus;
  provider: string;
  config_fingerprint: string;
  base_image_revision: string;
  provider_snapshot_ref?: string | null;
  error?: string | null;
  created_at?: string | null;
  ready_at?: string | null;
}

export interface EnvironmentView {
  config: RuntimeConfig;
  profile: ResourceProfile;
  config_fingerprint: string;
  reusable_revision_id?: string | null;
  revisions: ImageRevision[];
  /** Server-provided wording: what survives a stop is the provider's property. */
  persistence_note: string;
}

export interface ExecutionArtifact {
  path: string;
  size_bytes: number;
  content_type: string;
  downloadable: boolean;
}

export interface Execution {
  execution_id: string;
  workspace_id: string;
  mode: ExecutionMode;
  status: ExecutionStatus;
  source: string;
  runtime_id?: string | null;
  exit_code?: number | null;
  stdout: string;
  stderr: string;
  stdout_truncated: boolean;
  stderr_truncated: boolean;
  error?: string | null;
  artifacts: ExecutionArtifact[];
  duration_ms: number;
  memory_peak_bytes: number;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  /** False while a worker could still be working on it — the poll signal. */
  settled: boolean;
}

export interface ExecutionList {
  executions: Execution[];
  total: number;
  page: number;
  page_size: number;
}

export interface WorkspaceArtifact {
  execution_id: string;
  path: string;
  size_bytes: number;
  content_type: string;
  downloadable: boolean;
}

export interface WorkspaceFileEntry {
  path: string;
  name: string;
  is_dir: boolean;
  size_bytes: number;
}

export interface WorkspaceFileList {
  path: string;
  entries: WorkspaceFileEntry[];
}

export interface WorkspaceFileContent {
  path: string;
  size_bytes: number;
  content: string;
  encoding: "utf-8" | "base64";
  truncated: boolean;
}

export interface WorkspaceCreateRequest {
  name: string;
  description?: string;
  runtime_config?: Partial<Pick<RuntimeConfig, "python_version" | "uv_version" | "resource_profile" | "base_image_revision">>;
}

export interface WorkspaceUpdateRequest {
  name?: string;
  description?: string;
  runtime_config?: Partial<Pick<RuntimeConfig, "python_version" | "uv_version" | "resource_profile" | "base_image_revision">>;
}

export interface ExecutionCreateRequest {
  mode: ExecutionMode;
  source: string;
  timeout_seconds?: number;
  idempotency_key?: string;
}
