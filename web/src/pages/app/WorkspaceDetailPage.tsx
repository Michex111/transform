import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowClockwise,
  ArrowLeft,
  CaretRight,
  DownloadSimple,
  FloppyDisk,
  FolderOpen,
  Play,
  Stop,
  Terminal,
  WarningCircle,
} from "@phosphor-icons/react";
import type {
  EnvironmentView,
  Execution,
  ExecutionArtifact,
  ExecutionMode,
  Workspace,
  WorkspaceFileEntry,
} from "@/api/workspaceTypes";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { CodeBlock } from "@/components/CodeBlock";
import { Dropdown } from "@/components/Dropdown";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { APP_NAME } from "@/lib/documentTitle";
import type { StateTone } from "@/lib/workspaces";
import {
  canSaveEnvironment,
  canStartRuntime,
  canStopRuntime,
  executionAriaLabel,
  executionOutcomeMeta,
  formatBytes,
  formatDuration,
  formatTimestamp,
  isRuntimeTransitioning,
  nextStartSummary,
  pathBreadcrumb,
  runtimeBlocksConfigChange,
  runtimeStateMeta,
} from "@/lib/workspaces";

/** Map a status tone onto the Badge colour token. */
function toneColor(tone: StateTone): string {
  if (tone === "ok") return "var(--color-success)";
  if (tone === "busy") return "var(--color-info)";
  if (tone === "error") return "var(--color-error)";
  return "var(--color-muted)";
}

/** The runtime action in flight, phrased as progress rather than as a state. */
const PENDING_LABELS: Record<string, string> = {
  start: "Starting…",
  restart: "Restarting…",
  stop: "Stopping…",
};

/**
 * The execution modes, with the label the user sees.
 *
 * Declared at module scope so the options list is referentially stable: it is a
 * constant, and rebuilding it every render would make the dropdown's `options`
 * prop change identity on every keystroke in the source editor.
 */
const MODE_OPTIONS: { value: ExecutionMode; label: string }[] = [
  { value: "SCRIPT", label: "Python script" },
  { value: "COMMAND", label: "Shell command" },
  { value: "PROJECT", label: "uv project operation" },
];

/**
 * A single-line input, using the same tokens as the rest of the app's forms —
 * and deliberately the same ones as `Dropdown`'s trigger (`border-outline-strong`,
 * `bg-surface-variant`, `focus:border-primary`, h-9, 8px radius). Input and
 * dropdown sit side by side in the Execute toolbar, so they have to agree on
 * height and colour or the row looks assembled from parts.
 *
 * Padding is left to each call site: `px-3` here and `px-2` there would be two
 * `px-*` rules competing in the generated stylesheet, where the winner is
 * Tailwind's output order, not the order they appear in the class attribute.
 */
const INPUT_CLASS =
  "h-9 rounded-lg border border-outline-strong bg-surface-variant text-sm text-on-background focus:border-primary focus:outline-none";

/**
 * The code surface, matching `CodeBlock` — the same `border-outline` /
 * `bg-surface-sunken` pairing, so the source editor and the output panes read as
 * one thing. It adds the focus treatment `CodeBlock` does not need, because this
 * one is editable.
 */
const CODE_INPUT_CLASS =
  "rounded-lg border border-outline bg-surface-sunken font-mono text-sm text-on-background focus:border-primary focus:outline-none";

/** How often an in-flight execution is polled. Chosen to be gentle on the API. */
const POLL_INTERVAL_MS = 2_000;
/** Hard stop on polling, so a run that never settles cannot poll forever. */
const MAX_POLLS = 150;

/**
 * One workspace: its runtime, its code, its history and its environment.
 *
 * Three decisions shape this page:
 *
 * * **The runtime state drives the controls.** Start/Stop are enabled from
 *   `canStartRuntime`/`canStopRuntime`, so an action the server would reject is
 *   never offered — and every action is disabled while a transition is in
 *   flight, so a double-click cannot ask twice.
 * * **Polling is bounded and stops.** An in-flight execution is polled on a
 *   fixed interval until it settles or the bound is reached; there is no
 *   `setInterval` that outlives the run.
 * * **Nothing is simulated.** Output, exit codes and revision statuses are
 *   whatever the server returned. The page states what a stop preserves using
 *   the server's own wording rather than inventing a promise.
 */
export function WorkspaceDetailPage() {
  const { workspaceId = "" } = useParams<{ workspaceId: string }>();
  const { api: client } = useAuth();
  const { success, error: toastError } = useToast();

  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [environment, setEnvironment] = useState<EnvironmentView | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [savingEnvironment, setSavingEnvironment] = useState(false);

  const [mode, setMode] = useState<ExecutionMode>("SCRIPT");
  const [source, setSource] = useState("print('hello from your workspace')\n");
  const [timeoutSeconds, setTimeoutSeconds] = useState(300);
  const [submitting, setSubmitting] = useState(false);
  const [active, setActive] = useState<Execution | null>(null);

  const [files, setFiles] = useState<WorkspaceFileEntry[] | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<{ path: string; content: string } | null>(null);
  /** Directory being listed. `"."` is the workspace root. */
  const [filesPath, setFilesPath] = useState(".");
  /**
   * Bumped to re-run the files effect.
   *
   * The listing has to be re-read after anything that can change it — a run
   * settling, an environment saved, or the user asking. Keying the effect on a
   * counter rather than on a piece of derived state keeps those triggers
   * explicit instead of accidental.
   */
  const [filesVersion, setFilesVersion] = useState(0);
  const [downloading, setDownloading] = useState<string | null>(null);
  /**
   * The profiles the server will accept. `null` until loaded.
   *
   * Read from the API rather than hard-coded: which profiles exist is the
   * deployment's business, and a list baked into the bundle would offer a
   * profile a given deployment does not register.
   */
  const [profiles, setProfiles] = useState<string[] | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);

  const runtimeState = workspace?.runtime?.state ?? null;

  const loadWorkspace = useCallback(async () => {
    const [loadedWorkspace, loadedEnvironment] = await Promise.all([
      client.getWorkspace(workspaceId),
      client.workspaceEnvironment(workspaceId),
    ]);
    setWorkspace(loadedWorkspace);
    setEnvironment(loadedEnvironment);
    return loadedWorkspace;
  }, [client, workspaceId]);

  const loadExecutions = useCallback(async () => {
    const listing = await client.listWorkspaceExecutions(workspaceId, 1, 20);
    setExecutions(listing.executions);
    return listing.executions;
  }, [client, workspaceId]);

  const reloadEnvironment = useCallback(async () => {
    setEnvironment(await client.workspaceEnvironment(workspaceId));
  }, [client, workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [loaded] = await Promise.all([loadWorkspace(), loadExecutions()]);
        if (!cancelled) {
          setLoadFailed(false);
          // Resume showing the newest run, so returning to the page recovers
          // real status from the backend rather than assuming the session is new.
          void loaded;
        }
      } catch {
        if (!cancelled) setLoadFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadWorkspace, loadExecutions]);

  // Which profiles this deployment offers. Failure is not fatal: the selector
  // simply does not appear, rather than offering options that may not exist.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const configuration = await client.workspaceRuntimeConfiguration(workspaceId);
        if (!cancelled) setProfiles(configuration.supported_resource_profiles);
      } catch {
        if (!cancelled) setProfiles([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId]);

  // --- bounded polling of an in-flight execution -------------------------
  const activeId = active?.execution_id;
  const activeSettled = active?.settled ?? true;
  useEffect(() => {
    if (!activeId || activeSettled) return;
    let cancelled = false;
    let polls = 0;
    const timer = window.setInterval(() => {
      polls += 1;
      if (polls > MAX_POLLS) {
        window.clearInterval(timer);
        return;
      }
      void (async () => {
        try {
          const next = await client.execution(activeId);
          if (cancelled) return;
          setActive(next);
          setExecutions((prev) => {
            const others = prev.filter((item) => item.execution_id !== next.execution_id);
            return [next, ...others];
          });
          if (next.settled) window.clearInterval(timer);
        } catch {
          // A transient failure is not a settled run: keep the interval, which
          // the poll bound above still terminates.
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeId, activeSettled, client]);

  // A settled run is exactly when its files changed. Refresh the listing on that
  // edge so a script's output appears without the user having to discover a
  // refresh control.
  const settledRun = active?.settled ? active.execution_id : null;
  useEffect(() => {
    if (!settledRun) return;
    setFilesVersion((version) => version + 1);
  }, [settledRun]);

  // A snapshot is built asynchronously, so a revision saved just now is PENDING
  // and becomes READY (or FAILED) later. Without this the page showed PENDING
  // indefinitely and the user's only recourse was a manual reload — the same
  // information the API was already happy to give us.
  //
  // Bounded like the execution poll and stopped as soon as nothing is in flight;
  // the guard is on `shouldPollRevisions` rather than the revision list so a
  // render that changes nothing cannot restart the timer.
  const shouldPollRevisions =
    environment?.revisions.some(
      (revision) => revision.status === "PENDING" || revision.status === "BUILDING",
    ) ?? false;
  useEffect(() => {
    if (!shouldPollRevisions) return;
    let cancelled = false;
    let polls = 0;
    const timer = window.setInterval(() => {
      polls += 1;
      if (polls > MAX_POLLS) {
        window.clearInterval(timer);
        return;
      }
      void (async () => {
        try {
          if (cancelled) return;
          await reloadEnvironment();
        } catch {
          // A transient failure is not a failed build; the poll bound still
          // terminates this loop.
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [shouldPollRevisions, reloadEnvironment]);

  // --- files (only meaningful while a runtime can be talked to) ----------
  const runtimeRunning = runtimeState === "RUNNING";
  useEffect(() => {
    if (!runtimeRunning) {
      setFiles(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const listing = await client.workspaceFiles(workspaceId, filesPath);
        if (!cancelled) {
          setFiles(listing.entries);
          setFileError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setFiles([]);
          setFileError(err instanceof Error ? err.message : "Could not list the files");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, runtimeRunning, workspaceId, filesPath, filesVersion]);

  async function runRuntimeAction(
    label: string,
    action: () => Promise<unknown>,
    pending: keyof typeof PENDING_LABELS,
  ) {
    setRuntimeBusy(true);
    setPendingAction(pending);
    try {
      await action();
      await loadWorkspace();
      success(label);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "The runtime action failed");
      // Re-read anyway: the server may have recorded a FAILED state this page
      // would otherwise not show.
      try {
        await loadWorkspace();
      } catch {
        /* keep the last known state */
      }
    } finally {
      setRuntimeBusy(false);
      setPendingAction(null);
    }
  }

  async function submitExecution() {
    if (!source.trim()) {
      toastError("Write some code to run first.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await client.submitWorkspaceExecution(workspaceId, {
        mode,
        source,
        timeout_seconds: timeoutSeconds,
      });
      setActive(created);
      setExecutions((prev) => [created, ...prev]);
      success("Execution queued");
      await loadWorkspace();
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not submit the execution");
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelActive() {
    if (!active) return;
    try {
      const cancelledExecution = await client.cancelExecution(active.execution_id);
      setActive(cancelledExecution);
      success("Execution cancelled");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not cancel the execution");
    }
  }

  async function saveEnvironment() {
    setSavingEnvironment(true);
    try {
      const revision = await client.saveWorkspaceEnvironment(workspaceId);
      success(
        revision.status === "PENDING"
          ? "Saving your environment. It will appear as READY when the snapshot is built."
          : "Environment saved",
      );
      await loadWorkspace();
      // A snapshot can capture or change managed files, so do not keep showing a
      // listing that predates it.
      setFilesVersion((version) => version + 1);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not save the environment");
    } finally {
      setSavingEnvironment(false);
    }
  }

  async function changeProfile(profile: string) {
    setSavingProfile(true);
    try {
      const updated = await client.updateWorkspace(workspaceId, {
        runtime_config: { resource_profile: profile },
      });
      setWorkspace(updated);
      success(`Resource profile set to ${profile}`);
      // The saved-environment hint and the reusable revision both key off the
      // config fingerprint, which has just changed.
      await reloadEnvironment();
    } catch (err) {
      toastError(
        err instanceof Error ? err.message : "Could not change the resource profile",
      );
    } finally {
      setSavingProfile(false);
    }
  }

  async function downloadArtifact(artifact: ExecutionArtifact) {    if (!active) return;
    setDownloading(artifact.path);
    try {
      const { url } = await client.artifactDownload(active.execution_id, artifact.path);
      // The API signed a short-lived URL after checking ownership; the browser
      // does the transfer so the bytes never pass through this page.
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (err) {
      toastError(
        err instanceof Error ? err.message : "Could not prepare that download",
      );
    } finally {
      setDownloading(null);
    }
  }

  async function openWorkspaceFile(entry: WorkspaceFileEntry) {
    try {
      const file = await client.readWorkspaceFile(workspaceId, entry.path);
      setOpenFile({
        path: file.path,
        content:
          file.encoding === "base64"
            ? `(binary file, ${formatBytes(file.size_bytes)})`
            : file.content,
      });
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not read that file");
    }
  }

  // A tab reading "d3f1a4b2…" would be useless, so the page names itself once it
  // knows what it is. This runs after the shell's path-based title, so it wins
  // for as long as the user is on this page.
  //
  // Deliberately above the early returns below: a hook after a conditional
  // `return` changes the hook order between renders, which React forbids.
  useEffect(() => {
    if (!workspace) return;
    document.title = `${workspace.name} · ${APP_NAME}`;
  }, [workspace]);

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-4" aria-busy="true">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }

  if (loadFailed || !workspace || !environment) {
    return (
      <div className="mx-auto max-w-5xl">
        <div role="alert" className="rounded-xl border border-error/40 bg-error/10 p-4">
          <p className="flex items-center gap-2 text-sm text-on-background">
            <WarningCircle size={18} className="text-error" aria-hidden />
            This workspace could not be loaded. It may have been deleted.
          </p>
          <div className="mt-3 flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setLoading(true);
                void loadWorkspace()
                  .then(() => setLoadFailed(false))
                  .catch(() => setLoadFailed(true))
                  .finally(() => setLoading(false));
              }}
            >
              Try again
            </Button>
            <Link
              to="/app/workspaces"
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold text-muted hover:text-on-background"
            >
              <ArrowLeft size={14} aria-hidden />
              All workspaces
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const runtimeMeta = runtimeStateMeta(runtimeState);
  // Either the server says a transition is in flight, or this page has a request
  // outstanding. Both must block a second action.
  const transitioning = isRuntimeTransitioning(runtimeState) || runtimeBusy;
  const canStart = canStartRuntime(runtimeState) && !transitioning;
  const canStop = canStopRuntime(runtimeState) && !transitioning;
  const inFlight = active ? !active.settled : false;
  // The server refuses an environment change while the runtime is busy
  // (`_BUSY_RUNTIME_STATES`), so the control is locked rather than allowed to
  // fail — and the help text says why, because a disabled control with no reason
  // is worse than no control.
  const profileLocked = runtimeBlocksConfigChange(runtimeState);
  const canChooseProfile = profiles !== null && profiles.length > 1;
  // While this page's own request is outstanding the store is unchanged, so the
  // badge would still read "Stopped" for a second or more after a click. Say what
  // is actually happening instead.
  const badge = pendingAction
    ? {
        label: PENDING_LABELS[pendingAction] ?? "Working…",
        tone: "busy" as const,
        hint: "Waiting for the runtime provider to confirm.",
      }
    : runtimeMeta;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to="/app/workspaces"
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted hover:text-on-background"
          >
            <ArrowLeft size={12} aria-hidden />
            Workspaces
          </Link>
          <h1 className="mt-1 truncate font-display text-2xl font-semibold text-on-background">
            {workspace.name}
          </h1>
          {workspace.description && (
            <p className="mt-1 text-sm text-muted">{workspace.description}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge color={toneColor(badge.tone)} pulse={badge.tone === "busy"}>
            {badge.label}
          </Badge>
          <Button
            onClick={() =>
              void runRuntimeAction(
                "Runtime started",
                () => client.startWorkspaceRuntime(workspaceId),
                "start",
              )
            }
            disabled={!canStart}
          >
            <Play size={14} weight="fill" aria-hidden />
            Start
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              void runRuntimeAction(
                "Runtime restarted",
                () => client.restartWorkspaceRuntime(workspaceId),
                "restart",
              )
            }
            disabled={transitioning || runtimeState === null}
          >
            Restart
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              void runRuntimeAction(
                "Runtime stopped",
                () => client.stopWorkspaceRuntime(workspaceId),
                "stop",
              )
            }
            disabled={!canStop}
          >
            <Stop size={14} weight="fill" aria-hidden />
            Stop
          </Button>
        </div>
      </header>

      <Card className="p-4">
        <p className="text-sm text-muted">{badge.hint}</p>
        {runtimeState === "RUNNING" || runtimeState === "STOPPED" ? (
          <p className="mt-1 text-xs text-muted">
            {nextStartSummary(runtimeState, environment.reusable_revision_id != null)}
          </p>
        ) : null}
        {workspace.runtime?.failure_reason && (
          <p className="mt-2 rounded-lg border border-error/30 bg-error/5 px-3 py-2 font-mono text-xs text-error">
            {workspace.runtime.failure_reason}
          </p>
        )}
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-muted">Runtime</dt>
            <dd className="font-mono text-on-background">
              {workspace.runtime?.runtime_id.slice(0, 8) ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Provider</dt>
            <dd className="font-mono text-on-background">
              {workspace.runtime?.provider ?? "docker"}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Profile</dt>
            <dd className="font-mono text-on-background">
              {workspace.runtime_config.resource_profile}
            </dd>
          </div>
          <div>
            <dt className="text-muted">Started</dt>
            <dd className="text-on-background">
              {formatTimestamp(workspace.runtime?.started_at)}
            </dd>
          </div>
        </dl>
      </Card>

      {/* ---------------------------------------------------------------- */}
      <section aria-labelledby="execute-heading" className="space-y-3">
        <h2
          id="execute-heading"
          className="flex items-center gap-2 font-display text-lg font-semibold text-on-background"
        >
          <Terminal size={18} aria-hidden />
          Execute
        </h2>

        <Card className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            {/*
              The app's Dropdown, not a native `<select>`. The native control
              opens an OS-drawn menu that samples nothing from the tokens in
              `index.css`, so it was the one place in the product that ignored
              the theme entirely.
            */}
            <Dropdown
              label="Mode"
              ariaLabel="Execution mode"
              value={mode}
              onChange={setMode}
              options={MODE_OPTIONS}
            />
            <label
              htmlFor="execution-timeout"
              className="ml-auto text-sm text-muted"
            >
              Timeout
            </label>
            <input
              id="execution-timeout"
              type="number"
              min={1}
              max={3600}
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(Number(event.target.value) || 300)}
              className={`w-20 px-2 ${INPUT_CLASS}`}
            />
            <span className="text-xs text-muted">seconds</span>
          </div>

          <label htmlFor="execution-source" className="sr-only">
            Source
          </label>
          <textarea
            id="execution-source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            rows={8}
            spellCheck={false}
            className={`w-full px-3 py-2 ${CODE_INPUT_CLASS}`}
            aria-describedby="execution-help"
          />
          <p id="execution-help" className="text-xs text-muted">
            {mode === "SCRIPT"
              ? "Runs with uv run against this workspace's project, so packages you installed are importable."
              : mode === "COMMAND"
                ? "Run as a shell command line inside the workspace directory."
                : "One of: sync, lock, add <package>. Options are refused, so nothing can be installed from an unexpected index."}
          </p>

          <div className="flex flex-wrap items-center justify-end gap-2">
            {inFlight && (
              <Button variant="secondary" onClick={() => void cancelActive()}>
                Cancel run
              </Button>
            )}
            <Button onClick={() => void submitExecution()} disabled={submitting}>
              {submitting ? "Submitting…" : "Run"}
            </Button>
          </div>
        </Card>

        {active ? (
          <Card className="space-y-3 p-4" data-testid="active-execution">
            <div className="flex flex-wrap items-center gap-2">
              {/* The outcome, not the platform status: an exit of 1 is reported
                  as "Exited 1" with an error tone rather than as success. */}
              <Badge
                color={toneColor(executionOutcomeMeta(active).tone)}
                pulse={executionOutcomeMeta(active).tone === "busy"}
              >
                {executionOutcomeMeta(active).label}
              </Badge>
              <span className="font-mono text-xs text-muted">
                {active.mode} · {formatDuration(active.duration_ms)}
              </span>
              {!active.settled && (
                <span className="text-xs text-muted">Refreshing every 2s…</span>
              )}
            </div>
            <p className="text-xs text-muted">{executionOutcomeMeta(active).hint}</p>
            {active.error && (
              <p className="rounded-lg border border-error/30 bg-error/5 px-3 py-2 text-xs text-error">
                {active.error}
              </p>
            )}
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                stdout
              </h3>
              {active.stdout ? (
                <CodeBlock code={active.stdout} label="stdout" maxHeightClass="max-h-64" />
              ) : (
                <p className="text-xs text-muted">
                  {active.settled ? "No output." : "No output yet."}
                </p>
              )}
              {active.stdout_truncated && (
                <p className="mt-1 text-xs text-warning">
                  Output was longer than the capture limit; only the end is shown.
                </p>
              )}
            </div>
            {active.stderr && (
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                  stderr
                </h3>
                <CodeBlock code={active.stderr} label="stderr" maxHeightClass="max-h-64" />
                {active.stderr_truncated && (
                  <p className="mt-1 text-xs text-warning">
                    Error output was longer than the capture limit.
                  </p>
                )}
              </div>
            )}
            {active.artifacts.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                  Artifacts
                </h3>
                <ul className="space-y-1">
                  {active.artifacts.map((artifact) => (
                    <li
                      key={artifact.path}
                      className="flex items-center justify-between gap-3 rounded-lg border border-outline px-3 py-1.5 text-xs"
                    >
                      <span className="min-w-0 truncate font-mono text-on-background">
                        {artifact.path}
                      </span>
                      <span className="shrink-0 text-muted">
                        {formatBytes(artifact.size_bytes)}
                      </span>
                      {artifact.downloadable ? (
                        <button
                          type="button"
                          onClick={() => void downloadArtifact(artifact)}
                          disabled={downloading === artifact.path}
                          aria-label={`Download ${artifact.path}`}
                          className="shrink-0 rounded-md p-1 text-muted transition-colors hover:bg-surface-variant hover:text-on-background disabled:opacity-60"
                        >
                          <DownloadSimple size={14} aria-hidden />
                        </button>
                      ) : (
                        // Honest about why there is nothing to fetch: the file was
                        // produced but never copied out of the runtime.
                        <span className="shrink-0 text-muted" title="This file was not copied out of the runtime, so there is nothing to download.">
                          not stored
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        ) : null}
      </section>

      {/* ---------------------------------------------------------------- */}
      <section aria-labelledby="history-heading" className="space-y-3">
        <h2
          id="history-heading"
          className="font-display text-lg font-semibold text-on-background"
        >
          Executions
        </h2>
        {executions.length === 0 ? (
          <Card className="p-6 text-center text-sm text-muted">
            Nothing has run in this workspace yet.
          </Card>
        ) : (
          <Card className="divide-y divide-outline">
            {executions.map((execution) => {
              const meta = executionOutcomeMeta(execution);
              return (
                <button
                  type="button"
                  key={execution.execution_id}
                  onClick={() => setActive(execution)}
                  aria-label={executionAriaLabel(execution, formatTimestamp(execution.created_at))}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-variant/50"
                >
                  <CaretRight size={12} className="text-muted" aria-hidden />
                  <Badge color={toneColor(meta.tone)}>{meta.label}</Badge>
                  <span className="font-mono text-xs text-muted">{execution.mode}</span>
                  <span className="ml-auto text-xs text-muted">
                    {formatTimestamp(execution.created_at)}
                  </span>
                  <span className="w-16 text-right text-xs text-muted">
                    {formatDuration(execution.duration_ms)}
                  </span>
                </button>
              );
            })}
          </Card>
        )}
      </section>

      {/* ---------------------------------------------------------------- */}
      <section aria-labelledby="environment-heading" className="space-y-3">
        <h2
          id="environment-heading"
          className="font-display text-lg font-semibold text-on-background"
        >
          Environment
        </h2>
        <Card className="space-y-3 p-4">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted">Python</dt>
              <dd className="font-mono text-on-background">
                {environment.config.python_version}
              </dd>
            </div>
            <div>
              <dt className="text-muted">uv</dt>
              <dd className="font-mono text-on-background">
                {environment.config.uv_version}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Base image</dt>
              <dd className="font-mono text-on-background">
                {environment.config.image_ref}
              </dd>
            </div>
          </dl>

          {/*
            Resource profile — a control, not a fact.

            Two reasons it lives here rather than in the runtime summary above:
            that summary is a grid of read-only facts (an editable input among
            `text-xs` values reads as a mistake), and this is where the
            environment's resources are described, so it is where someone looks
            to change them. The create dialog promises the profile can be changed
            later, and this is what makes that sentence true.
          */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-outline bg-surface-variant/40 px-3 py-2">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-on-background">Resource profile</p>
              <p id="resource-profile-help" className="text-xs text-muted">
                {environment.profile.cpu_cores} CPU · {environment.profile.memory_mb} MB
                memory · {environment.profile.disk_mb} MB disk
                {profileLocked
                  ? " — stop the runtime to change it."
                  : " — a change applies the next time the runtime starts."}
              </p>
            </div>
            {canChooseProfile ? (
              <Dropdown
                ariaLabel="Resource profile"
                ariaDescribedBy="resource-profile-help"
                value={workspace.runtime_config.resource_profile}
                onChange={(value) => void changeProfile(value)}
                options={profiles.map((profile) => ({
                  value: profile,
                  label: profile,
                }))}
                disabled={savingProfile || profileLocked}
              />
            ) : (
              // Fewer than two profiles to choose from, or the server did not
              // say which exist: state the fact rather than offer a control that
              // cannot change anything.
              <span className="font-mono text-xs text-on-background">
                {workspace.runtime_config.resource_profile}
              </span>
            )}
          </div>

          <p className="rounded-lg border border-outline bg-surface-variant/40 px-3 py-2 text-xs text-muted">
            {environment.persistence_note}
          </p>

          {environment.reusable_revision_id && (
            <p className="text-xs text-success">
              A saved snapshot matches this configuration and will be restored on the
              next start.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => void saveEnvironment()}
              disabled={!canSaveEnvironment(runtimeState) || savingEnvironment}
            >
              <FloppyDisk size={14} aria-hidden />
              {savingEnvironment ? "Saving…" : "Save environment"}
            </Button>
            {!canSaveEnvironment(runtimeState) && (
              <span className="text-xs text-muted">Start the runtime to save it.</span>
            )}
          </div>

          {environment.revisions.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                Revisions
              </h3>
              <ul className="space-y-1">
                {environment.revisions.map((revision) => (
                  <li
                    key={revision.revision_id}
                    className="flex flex-wrap items-center gap-2 rounded-lg border border-outline px-3 py-1.5 text-xs"
                  >
                    <Badge
                      color={
                        revision.status === "READY"
                          ? "var(--color-success)"
                          : revision.status === "FAILED"
                            ? "var(--color-error)"
                            : "var(--color-info)"
                      }
                      pulse={revision.status === "BUILDING" || revision.status === "PENDING"}
                    >
                      {revision.status}
                    </Badge>
                    <span className="font-mono text-muted">
                      {revision.base_image_revision}
                    </span>
                    <span className="ml-auto text-muted">
                      {formatTimestamp(revision.created_at)}
                    </span>
                    {revision.error && (
                      <span className="w-full text-error">{revision.error}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </section>

      {/* ---------------------------------------------------------------- */}
      <section aria-labelledby="files-heading" className="space-y-3">
        <h2
          id="files-heading"
          className="flex items-center gap-2 font-display text-lg font-semibold text-on-background"
        >
          <FolderOpen size={18} aria-hidden />
          Files
        </h2>
        <Card className="space-y-3 p-4">
          {!runtimeRunning ? (
            <p className="text-sm text-muted">
              Start the runtime to browse the files in this workspace.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <nav aria-label="Current folder" className="flex flex-wrap items-center gap-1 text-xs">
                  {pathBreadcrumb(filesPath).map((crumb, index, trail) => (
                    <span key={crumb.path} className="flex items-center gap-1">
                      {index > 0 && (
                        <span className="text-muted" aria-hidden>
                          /
                        </span>
                      )}
                      {index === trail.length - 1 ? (
                        <span className="font-mono text-on-background">{crumb.label}</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setFilesPath(crumb.path)}
                          className="rounded font-mono text-muted transition-colors hover:text-on-background"
                        >
                          {crumb.label}
                        </button>
                      )}
                    </span>
                  ))}
                </nav>
                <button
                  type="button"
                  onClick={() => setFilesVersion((version) => version + 1)}
                  aria-label="Refresh files"
                  className="ml-auto rounded-md p-1 text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
                >
                  <ArrowClockwise size={14} aria-hidden />
                </button>
              </div>

              {fileError ? (
                <p className="text-sm text-error">{fileError}</p>
              ) : files === null ? (
                <Skeleton className="h-16 w-full" />
              ) : files.length === 0 ? (
                <p className="text-sm text-muted">
                  {filesPath === "."
                    ? "Nothing here yet. A script that writes a file will show up above."
                    : "This folder is empty."}
                </p>
              ) : (
                <ul className="divide-y divide-outline">
                  {files.map((entry) => (
                    <li key={entry.path}>
                      <button
                        type="button"
                        onClick={() =>
                          entry.is_dir
                            ? setFilesPath(entry.path)
                            : void openWorkspaceFile(entry)
                        }
                        className="flex w-full items-center gap-2 py-2 text-left text-xs transition-colors hover:text-on-background"
                      >
                        <span className="font-mono text-on-background">{entry.name}</span>
                        {entry.is_dir ? (
                          <span className="text-muted">folder</span>
                        ) : (
                          <span className="ml-auto text-muted">
                            {formatBytes(entry.size_bytes)}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Card>

        {openFile && (
          <CodeBlock
            code={openFile.content}
            label={openFile.path}
            maxHeightClass="max-h-80"
          />
        )}
      </section>
    </div>
  );
}
