/**
 * Presentation rules for workspaces: runtime/execution state wording and the
 * arithmetic the pages would otherwise repeat.
 *
 * Kept out of the page components for the same reason `lib/*` exists elsewhere
 * in this app: these are pure functions a test can pin, and they encode
 * decisions that must not drift between the list and the detail view — most
 * importantly *which actions are legal in which state*, because offering a
 * Stop button on a stopped runtime is how a user learns to distrust the UI.
 */

import type { ExecutionStatus, RuntimeState } from "@/api/workspaceTypes";

/** How a state should read, and whether it is good, busy, bad or neutral. */
export type StateTone = "ok" | "busy" | "error" | "idle";

export interface StateMeta {
  label: string;
  tone: StateTone;
  /** One sentence a person can act on. Never claims more than the server said. */
  hint: string;
}

const RUNTIME_STATES: Record<RuntimeState, StateMeta> = {
  STOPPED: {
    label: "Stopped",
    tone: "idle",
    hint: "Files and configuration are kept. Start the runtime to run code again.",
  },
  STARTING: {
    label: "Starting",
    tone: "busy",
    hint: "The runtime is being allocated. This usually takes a few seconds.",
  },
  RUNNING: {
    label: "Running",
    tone: "ok",
    hint: "Executions can run now.",
  },
  STOPPING: {
    label: "Stopping",
    tone: "busy",
    hint: "The runtime is shutting down. Nothing durable is being removed.",
  },
  FAILED: {
    label: "Failed to start",
    tone: "error",
    hint: "Your workspace and its files are untouched. Starting again is the recovery path.",
  },
};

const EXECUTION_STATUSES: Record<ExecutionStatus, StateMeta> = {
  QUEUED: {
    label: "Queued",
    tone: "busy",
    hint: "Waiting for a worker to pick it up.",
  },
  RUNNING: { label: "Running", tone: "busy", hint: "Executing now." },
  SUCCEEDED: {
    // Deliberately not "Finished". ``SUCCEEDED`` is the *platform's* status —
    // it means "we ran your program" — and the program itself may have exited
    // non-zero. ``executionOutcomeMeta`` refines this with the exit code; on its
    // own this label is only correct for a clean exit.
    label: "Succeeded",
    tone: "ok",
    hint: "The program ran and exited 0.",
  },
  FAILED: { label: "Failed", tone: "error", hint: "The run did not complete." },
  CANCELLED: { label: "Cancelled", tone: "idle", hint: "You cancelled this run." },
  TIMED_OUT: {
    label: "Timed out",
    tone: "error",
    hint: "It exceeded its time limit and was stopped.",
  },
};

/** A state the server did not send is shown as neutral, never as healthy. */
const UNKNOWN_RUNTIME: StateMeta = {
  label: "Unknown",
  tone: "idle",
  hint: "The server reported a state this page does not recognise.",
};

const UNKNOWN_EXECUTION: StateMeta = {
  label: "Unknown",
  tone: "idle",
  hint: "The server reported a status this page does not recognise.",
};

/**
 * A missing runtime is *not* an unknown state.
 *
 * "This workspace has never been started" is a normal, known condition and the
 * user needs to read it as such; conflating it with "the server sent something
 * we do not understand" would show "Unknown" on every new workspace.
 */
export function runtimeStateMeta(state: RuntimeState | undefined | null): StateMeta {
  if (state === undefined || state === null) return RUNTIME_STATES.STOPPED;
  return RUNTIME_STATES[state] ?? UNKNOWN_RUNTIME;
}

export function executionStatusMeta(status: ExecutionStatus | undefined | null): StateMeta {
  if (!status) return UNKNOWN_EXECUTION;
  return EXECUTION_STATUSES[status] ?? UNKNOWN_EXECUTION;
}

/**
 * The status as the user should read it, including the program's own exit code.
 *
 * WHY this is separate from :func:`executionStatusMeta`: the platform's status
 * and the program's outcome are different facts, and conflating them produced a
 * genuine defect — a run that exited 1 rendered as a green
 * "Finished" chip, so the executions list and the detail panel both affirmed
 * success for a script that had failed. The exit code is part of the answer, not
 * a footnote.
 *
 * A non-zero exit is not a platform error: the run worked, the program reported a
 * problem. The wording says "Exited N" rather than "Failed" so it does not
 * blame the platform for the user's own traceback, while the error tone makes it
 * distinguishable without relying on that nuance.
 */
export function executionOutcomeMeta(execution: {
  status: ExecutionStatus | undefined | null;
  exit_code?: number | null;
}): StateMeta {
  const base = executionStatusMeta(execution.status);
  if (execution.status !== "SUCCEEDED") return base;
  const code = execution.exit_code;
  if (code === null || code === undefined) {
    // The process gave no status (killed before reporting). Not a success.
    return {
      label: "No exit code",
      tone: "error",
      hint: "The program did not report an exit status, so its result is unknown.",
    };
  }
  if (code === 0) return base;
  return {
    label: `Exited ${code}`,
    tone: "error",
    hint: `The program ran and exited ${code} — see stderr for what it reported.`,
  };
}

/** The one-line summary a screen reader hears for one execution. */
export function executionAriaLabel(
  execution: {
    status: ExecutionStatus | undefined | null;
    exit_code?: number | null;
    mode: string;
    duration_ms: number;
  },
  when: string,
): string {
  const meta = executionOutcomeMeta(execution);
  const measured =
    execution.status === "SUCCEEDED" && execution.exit_code !== null && execution.exit_code !== undefined
      ? `, exit code ${execution.exit_code}`
      : "";
  return `${meta.label}${measured}, ${execution.mode} run, ${formatDuration(
    execution.duration_ms,
  )}, ${when}`;
}

/**
 * Split a POSIX-ish workspace path into an ancestor chain for a breadcrumb.
 *
 * Pure string work rather than node's `path`: these paths come from the server
 * and are always forward-slash separated regardless of the platform the API or
 * the browser happens to be running on, and `path.win32` would mangle them.
 *
 * The first crumb is always the workspace root, so there is a way back from any
 * depth without the caller having to remember the root's spelling.
 */
export function pathBreadcrumb(path: string): { label: string; path: string }[] {
  const parts = path.split("/").filter((part) => part !== "" && part !== ".");
  const trail = [{ label: "workspace", path: "." }];
  let soFar = "";
  for (const part of parts) {
    soFar = soFar === "" ? part : `${soFar}/${part}`;
    trail.push({ label: part, path: soFar });
  }
  return trail;
}

/** True while a transition is in flight, so a second action must be blocked. */
export function isRuntimeTransitioning(state: RuntimeState | undefined | null): boolean {
  return state === "STARTING" || state === "STOPPING";
}

/**
 * Start is offered from STOPPED and FAILED only.
 *
 * FAILED is included on purpose: a failed start leaves the workspace intact, so
 * retrying is the documented recovery path rather than something to hide.
 */
export function canStartRuntime(state: RuntimeState | undefined | null): boolean {
  return state === undefined || state === null || state === "STOPPED" || state === "FAILED";
}

export function canStopRuntime(state: RuntimeState | undefined | null): boolean {
  return state === "RUNNING" || state === "STARTING";
}

/** A runtime must be up before a snapshot can be taken. */
export function canSaveEnvironment(state: RuntimeState | undefined | null): boolean {
  return state === "RUNNING";
}

/**
 * True while the server would refuse an environment change.
 *
 * Mirrors `_BUSY_RUNTIME_STATES` in the workspaces service. It is duplicated
 * here rather than fetched because the page needs it synchronously to decide
 * whether the control is enabled, and offering an edit the server will reject is
 * worse than duplicating three strings. STOPPED and FAILED are editable: FAILED
 * means the runtime never started, so there is nothing running to reconfigure.
 */
export function runtimeBlocksConfigChange(state: RuntimeState | undefined | null): boolean {
  return state === "STARTING" || state === "RUNNING" || state === "STOPPING";
}

/** Execution is submitted regardless of runtime state: the worker starts it. */
export function executionInFlight(status: ExecutionStatus | undefined | null): boolean {
  return status === "QUEUED" || status === "RUNNING";
}

export function formatDuration(durationMs: number): string {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return "—";
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  const seconds = durationMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}m ${remainder}s`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "kB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
}

/** Absolute, local, and short. `—` rather than "Invalid Date" for a missing one. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * How long ago, in words. Used for "last activity", where the exact time is
 * noise and the *recentness* is the point.
 */
export function formatRelative(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "never";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "never";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.round(days / 30);
  return `${months} month${months === 1 ? "" : "s"} ago`;
}

/**
 * The sentence that must accompany every statement about restarting.
 *
 * Stated once, and appended by every branch below, because it is the claim a
 * user is most likely to assume wrongly: "my variable was still set" is the
 * single most common misunderstanding of a persistent environment, and a branch
 * that omitted the caveat would imply the opposite by silence.
 */
const NOT_RESTORED =
  "Running processes and in-memory variables are not restored.";

/**
 * The label for the next start, which depends on what the next start will do.
 *
 * This is the one place the UI states a persistence claim, so it distinguishes
 * the three real cases instead of saying "resume" for all of them.
 */
export function nextStartSummary(
  state: RuntimeState | undefined | null,
  hasReusableSnapshot: boolean,
): string {
  if (state === "FAILED") {
    return `Starting again retries on the same workspace — your files were never touched. ${NOT_RESTORED}`;
  }
  if (state === "RUNNING") {
    return `The runtime is already running; nothing will be re-created. ${NOT_RESTORED}`;
  }
  if (hasReusableSnapshot) {
    return `This start restores your saved environment, then reconciles it with uv.lock. ${NOT_RESTORED}`;
  }
  return `This start reuses the existing runtime and reinstalls nothing, or rebuilds the environment from uv.lock if it was replaced. ${NOT_RESTORED}`;
}
