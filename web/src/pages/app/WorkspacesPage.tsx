import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Link } from "react-router-dom";
import {
  Archive,
  ArrowCounterClockwise,
  ArrowRight,
  Cpu,
  Play,
  Plus,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import type { Workspace } from "@/api/workspaceTypes";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { formatRelative, runtimeStateMeta } from "@/lib/workspaces";

/**
 * The workspace list.
 *
 * A workspace is a durable place, so this page answers three questions and
 * nothing else: which workspaces exist, whether each one's runtime is up, and
 * when it was last used. Starting a runtime is deliberately a detail-page action
 * — creating a workspace is cheap and reversible, whereas starting compute is
 * not, and offering it on every row invites accidental spend.
 *
 * The runtime state shown is whatever the server last reported. Nothing here is
 * optimistic: a card that said "Running" while the server said otherwise would
 * be worse than a card that lags by one refresh.
 */
export function WorkspacesPage() {
  const { api: client } = useAuth();
  const { success, error: toastError } = useToast();
  const reduce = useReducedMotion();

  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const [deleteTarget, setDeleteTarget] = useState<Workspace | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [archiveTarget, setArchiveTarget] = useState<Workspace | null>(null);
  const [archiving, setArchiving] = useState(false);
  /** The workspace whose archive/reactivate request is in flight. */
  const [busyWorkspaceId, setBusyWorkspaceId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await client.listWorkspaces(1, 100);
      setWorkspaces(response.workspaces);
      setLoadFailed(false);
    } catch {
      // An empty list after a failure would read as "you have no workspaces",
      // which is a lie; the error banner says what actually happened.
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submitCreate() {
    const trimmed = name.trim();
    if (!trimmed) {
      toastError("Give the workspace a name.");
      return;
    }
    setCreating(true);
    try {
      const created = await client.createWorkspace({
        name: trimmed,
        description: description.trim(),
      });
      setWorkspaces((prev) => [created, ...prev]);
      setCreateOpen(false);
      setName("");
      setDescription("");
      success("Workspace created. Start its runtime when you are ready.");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not create the workspace");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await client.deleteWorkspace(deleteTarget.workspace_id);
      setWorkspaces((prev) =>
        prev.filter((item) => item.workspace_id !== deleteTarget.workspace_id),
      );
      setDeleteTarget(null);
      success("Workspace deleted, along with its files and runtime.");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not delete that workspace");
    } finally {
      setDeleting(false);
    }
  }

  async function confirmArchive() {
    if (!archiveTarget) return;
    setArchiving(true);
    setBusyWorkspaceId(archiveTarget.workspace_id);
    try {
      // Archiving refuses new work but leaves compute alone, so a running
      // runtime would keep costing money with nothing able to use it. Stop it
      // first when one is up: that is what "archive it instead" has to mean to be
      // worth offering.
      if (archiveTarget.runtime?.state === "RUNNING") {
        await client.stopWorkspaceRuntime(archiveTarget.workspace_id);
      }
      const archived = await client.archiveWorkspace(archiveTarget.workspace_id);
      setWorkspaces((prev) =>
        prev.map((item) => (item.workspace_id === archived.workspace_id ? archived : item)),
      );
      setArchiveTarget(null);
      success("Workspace archived. Its files are kept and it will refuse new runs.");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not archive that workspace");
      // The stop may have succeeded even though the archive did not, so re-read
      // rather than leaving the card showing a runtime that is already gone.
      await refresh();
    } finally {
      setArchiving(false);
      setBusyWorkspaceId(null);
    }
  }

  async function reactivate(workspace: Workspace) {    setBusyWorkspaceId(workspace.workspace_id);
    try {
      const active = await client.reactivateWorkspace(workspace.workspace_id);
      setWorkspaces((prev) =>
        prev.map((item) => (item.workspace_id === active.workspace_id ? active : item)),
      );
      success("Workspace reactivated. It accepts runs again.");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not reactivate that workspace");
    } finally {
      setBusyWorkspaceId(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold text-on-background">
            Workspaces
          </h1>
          <p className="mt-1 text-sm text-muted">
            Persistent Python environments with their own files and dependencies.
            Stop a runtime whenever you like — your work stays.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus size={16} weight="bold" aria-hidden />
          New workspace
        </Button>
      </header>

      {loadFailed && (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-error/40 bg-error/10 p-4"
        >
          <span className="flex items-center gap-2 text-sm text-on-background">
            <WarningCircle size={18} className="text-error" aria-hidden />
            Could not load your workspaces.
          </span>
          <Button variant="secondary" size="sm" onClick={() => void refresh()}>
            Try again
          </Button>
        </div>
      )}

      {loading ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading workspaces">
          {[0, 1].map((index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      ) : workspaces.length === 0 ? (
        <Card className="p-8 text-center">
          <Cpu size={28} className="mx-auto text-muted" aria-hidden />
          <h2 className="mt-3 font-display text-lg font-semibold text-on-background">
            No workspaces yet
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted">
            A workspace gives you a Python environment that keeps its files and
            installed packages between sessions, and that an agent can use too.
          </p>
          <Button className="mt-4" onClick={() => setCreateOpen(true)}>
            Create your first workspace
          </Button>
        </Card>
      ) : (
        <ul className="space-y-3">
          <AnimatePresence initial={false}>
            {workspaces.map((workspace, index) => {
              const meta = runtimeStateMeta(workspace.runtime?.state);
              return (
                <motion.li
                  key={workspace.workspace_id}
                  initial={reduce ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={reduce ? undefined : { opacity: 0, y: -6 }}
                  transition={{ delay: Math.min(index, 6) * 0.02 }}
                >
                  <Card hover className="p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 className="truncate font-display text-base font-semibold text-on-background">
                            {workspace.name}
                          </h2>
                          <Badge
                            color={
                              meta.tone === "ok"
                                ? "var(--color-success)"
                                : meta.tone === "busy"
                                  ? "var(--color-info)"
                                  : meta.tone === "error"
                                    ? "var(--color-error)"
                                    : "var(--color-muted)"
                            }
                            pulse={meta.tone === "busy"}
                          >
                            {meta.label}
                          </Badge>
                          {workspace.state === "ARCHIVED" && (
                            <Badge color="var(--color-warning)">Archived</Badge>
                          )}
                        </div>
                        {workspace.description && (
                          <p className="mt-1 line-clamp-2 text-sm text-muted">
                            {workspace.description}
                          </p>
                        )}
                        <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
                          <div>
                            <dt className="inline">Python </dt>
                            <dd className="inline font-mono">
                              {workspace.runtime_config.python_version}
                            </dd>
                          </div>
                          <div>
                            <dt className="inline">uv </dt>
                            <dd className="inline font-mono">
                              {workspace.runtime_config.uv_version}
                            </dd>
                          </div>
                          <div>
                            <dt className="inline">Last used </dt>
                            <dd className="inline">
                              {formatRelative(workspace.last_activity_at)}
                            </dd>
                          </div>
                          <div>
                            <dt className="inline">Executions </dt>
                            <dd className="inline">{workspace.execution_count}</dd>
                          </div>
                        </dl>
                      </div>
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/app/workspaces/${workspace.workspace_id}`}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-outline px-3 py-1.5 text-sm font-semibold text-on-background transition-colors hover:border-primary/40"
                        >
                          Open
                          <ArrowRight size={14} aria-hidden />
                        </Link>
                        {workspace.state === "ARCHIVED" ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busyWorkspaceId === workspace.workspace_id}
                            onClick={() => void reactivate(workspace)}
                            aria-label={`Reactivate ${workspace.name}`}
                          >
                            <ArrowCounterClockwise size={16} aria-hidden />
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={busyWorkspaceId === workspace.workspace_id}
                            onClick={() => setArchiveTarget(workspace)}
                            aria-label={`Archive ${workspace.name}`}
                          >
                            <Archive size={16} aria-hidden />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteTarget(workspace)}
                          aria-label={`Delete ${workspace.name}`}
                        >
                          <Trash size={16} aria-hidden />
                        </Button>
                      </div>
                    </div>
                    {workspace.active_executions > 0 && (
                      <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">
                        <Play size={12} weight="fill" aria-hidden />
                        {workspace.active_executions} execution
                        {workspace.active_executions === 1 ? "" : "s"} in flight
                      </p>
                    )}
                  </Card>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="New workspace"
        description="Python 3.14 and a PINNED uv toolchain. You can change the resource profile later, from the workspace page, once its runtime is stopped."
      >
        <div className="space-y-4">
          <div>
            <label
              htmlFor="workspace-name"
              className="mb-1 block text-sm font-semibold text-on-background"
            >
              Name
            </label>
            <input
              id="workspace-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              autoFocus
              className="w-full rounded-lg border border-outline bg-surface-variant px-3 py-2 text-sm text-on-background"
              placeholder="Analytics scratchpad"
            />
          </div>
          <div>
            <label
              htmlFor="workspace-description"
              className="mb-1 block text-sm font-semibold text-on-background"
            >
              Description <span className="font-normal text-muted">(optional)</span>
            </label>
            <textarea
              id="workspace-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={400}
              rows={3}
              className="w-full rounded-lg border border-outline bg-surface-variant px-3 py-2 text-sm text-on-background"
              placeholder="What this workspace is for"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void submitCreate()} disabled={creating}>
              {creating ? "Creating…" : "Create workspace"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete this workspace?"
        description="This removes its runtime and EVERY file in it, including the environment snapshots. It cannot be undone."
      >
        <p className="text-sm text-muted">
          Deleting <strong className="text-on-background">{deleteTarget?.name}</strong>{" "}
          removes its files, virtual environment and execution history. If you only
          want to stop it accepting new runs for a while, archive it instead — that
          keeps every file.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
            Keep it
          </Button>
          <Button
            variant="destructive"
            onClick={() => void confirmDelete()}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete workspace"}
          </Button>
        </div>
      </Modal>

      <Modal
        open={archiveTarget !== null}
        onClose={() => setArchiveTarget(null)}
        title="Archive this workspace?"
        description="Archiving keeps every file and refuses new runs until you reactivate it."
      >
        <p className="text-sm text-muted">
          <strong className="text-on-background">{archiveTarget?.name}</strong> keeps its
          files, its installed packages and its history, and you can reactivate it at any
          time. While it is archived nothing can run in it.
        </p>
        {archiveTarget?.runtime?.state === "RUNNING" && (
          <p className="mt-2 text-sm text-warning">
            Its runtime is running and will be stopped first — an archived workspace cannot
            use it, so leaving it up would only cost money.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setArchiveTarget(null)}>
            Cancel
          </Button>
          <Button onClick={() => void confirmArchive()} disabled={archiving}>
            {archiving ? "Archiving…" : "Archive workspace"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
