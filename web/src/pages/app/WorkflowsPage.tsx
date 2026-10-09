import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  ArrowRight,
  ArrowsClockwise,
  CheckCircle,
  CircleNotch,
  FloppyDisk,
  Lightning,
  Plus,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import type { FileMetadataResponse, SavedWorkflow, WorkflowRunResponse } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { FormatThumb } from "@/components/FormatThumb";
import { FormatPicker } from "@/components/FormatPicker";
import { Button, Skeleton } from "@/components/ui";
import { useConversionMap } from "@/lib/useConversionMap";
import { workflowTargetOptions } from "@/lib/workflowTargets";

/**
 * Saved workflows: the list, and running one.
 *
 * A workflow is a *template* — it names the operations and the target formats,
 * and the files are chosen when it runs. That is why the run flow here always
 * starts from a file selection: there is no stored file list that could have
 * expired or been deleted, and nothing the server would have to explain away.
 *
 * Running is a batch, so the result is per item. The page never waits for a
 * conversion to finish: it starts jobs and the Queue page's existing progress
 * reporting takes over from there.
 */
export function WorkflowsPage() {
  const { api: client } = useAuth();
  const { success, error: toastError } = useToast();
  const reduce = useReducedMotion();

  const [workflows, setWorkflows] = useState<SavedWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  // Create/edit dialog.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<SavedWorkflow | null>(null);

  // Run dialog.
  const [runTarget, setRunTarget] = useState<SavedWorkflow | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<SavedWorkflow | null>(null);
  const [deleting, setDeleting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const response = await client.listWorkflows();
      setWorkflows(response.workflows);
      setLoadFailed(false);
    } catch {
      // Non-fatal: the empty state explains the failure rather than pretending
      // the account simply has no workflows.
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await client.deleteWorkflow(deleteTarget.workflow_id);
      setWorkflows((prev) => prev.filter((w) => w.workflow_id !== deleteTarget.workflow_id));
      setDeleteTarget(null);
      success("Workflow deleted");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not delete that workflow");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold">Workflows</h1>
          <p className="mt-1 text-sm text-muted">
            Save a conversion you run often, then apply it to any files you choose.
          </p>
        </div>
        <Button
          onClick={() => {
            setEditing(null);
            setEditorOpen(true);
          }}
        >
          <Plus size={16} weight="bold" /> New workflow
        </Button>
      </header>

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-24 w-full rounded-xl" />
          ))}
        </div>
      ) : loadFailed ? (
        <div className="rounded-xl border border-error/40 bg-error/10 px-4 py-6 text-center">
          <p className="text-sm text-error">Could not load your workflows.</p>
          <Button variant="secondary" size="sm" className="mt-3" onClick={() => void refresh()}>
            Try again
          </Button>
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState
          onCreate={() => {
            setEditing(null);
            setEditorOpen(true);
          }}
        />
      ) : (
        <ul className="space-y-3">
          <AnimatePresence initial={false}>
            {workflows.map((workflow, index) => (
              <motion.li
                key={workflow.workflow_id}
                initial={reduce ? false : { opacity: 0, y: 8 }}
                animate={reduce ? undefined : { opacity: 1, y: 0 }}
                transition={{ duration: 0.2, delay: reduce ? 0 : Math.min(index, 6) * 0.02 }}
              >
                <WorkflowCard
                  workflow={workflow}
                  onRun={() => setRunTarget(workflow)}
                  onEdit={() => {
                    setEditing(workflow);
                    setEditorOpen(true);
                  }}
                  onDelete={() => setDeleteTarget(workflow)}
                />
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}

      <WorkflowEditorModal
        open={editorOpen}
        workflow={editing}
        onClose={() => setEditorOpen(false)}
        onSaved={(saved) => {
          setEditorOpen(false);
          setWorkflows((prev) => {
            const exists = prev.some((w) => w.workflow_id === saved.workflow_id);
            return exists
              ? prev.map((w) => (w.workflow_id === saved.workflow_id ? saved : w))
              : [saved, ...prev];
          });
        }}
      />

      <RunWorkflowModal
        workflow={runTarget}
        onClose={() => setRunTarget(null)}
        onRan={() => {
          // Refresh so the run badge reflects the run that just started.
          void refresh();
        }}
      />

      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete workflow"
        description={deleteTarget?.name}
      >
        <div className="space-y-4">
          <p className="text-sm text-muted">
            This deletes the workflow. Conversions it already produced are kept in your history.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

/** The operations a workflow performs, as a readable sentence fragment. */
function describeOperations(workflow: SavedWorkflow): string {
  const targets = workflow.definition.operations.map((op) => op.target_format.toUpperCase());
  const unique = Array.from(new Set(targets));
  return `Convert to ${unique.join(", then ")}`;
}

function WorkflowCard({
  workflow,
  onRun,
  onEdit,
  onDelete,
}: {
  workflow: SavedWorkflow;
  onRun: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="rounded-xl border border-outline bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-display text-base font-semibold" title={workflow.name}>
            {workflow.name}
          </h2>
          {workflow.description && (
            <p className="mt-0.5 text-sm text-muted">{workflow.description}</p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <ArrowsClockwise size={13} aria-hidden />
              {describeOperations(workflow)}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Lightning size={13} aria-hidden />
              {workflow.run_count === 1 ? "Run once" : `Run ${workflow.run_count} times`}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" onClick={onRun}>
            <Lightning size={15} weight="fill" /> Run
          </Button>
          <Button variant="secondary" size="sm" onClick={onEdit} aria-label={`Edit ${workflow.name}`}>
            Edit
          </Button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`Delete workflow ${workflow.name}`}
            className="rounded-lg p-2 text-muted transition-colors hover:bg-error/10 hover:text-error pointer-coarse:min-h-11 pointer-coarse:min-w-11"
          >
            <Trash size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-xl border border-dashed border-outline-strong bg-surface-variant/30 px-6 py-10 text-center">
      <Lightning size={26} className="mx-auto text-primary" aria-hidden />
      <h2 className="mt-3 font-display text-base font-semibold">No workflows yet</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted">
        Save a conversion you repeat — for example &ldquo;convert to PDF&rdquo; — and you can
        apply it to any files you pick, without retyping the instructions.
      </p>
      <Button className="mt-4" onClick={onCreate}>
        <Plus size={16} weight="bold" /> New workflow
      </Button>
    </div>
  );
}

/**
 * Create/edit dialog.
 *
 * Only the operations the server accepts are offered: the target list comes from
 * the conversion map, so the UI cannot propose a format that would then be
 * rejected. Name and definition are saved together, so what the user reads and
 * what will run are never from different revisions.
 *
 * Exported for its test, which asserts the format control is the app's own
 * picker rather than a native `<select>` — the popover itself is stateful and
 * this project renders without a DOM, so the trigger's contract is what a
 * server render can prove.
 */
export function WorkflowEditorModal({
  open,
  workflow,
  onClose,
  onSaved,
}: {
  open: boolean;
  workflow: SavedWorkflow | null;
  onClose: () => void;
  onSaved: (saved: SavedWorkflow) => void;
}) {
  const { api: client } = useAuth();
  const { targets: allTargets, loading: mapLoading } = useConversionMap({ enabled: open });

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState("pdf");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Every distinct format that can be produced, across the whole map. A
  // workflow is applied to files chosen later, so the picker cannot be narrowed
  // to one source format — and the list comes from the server's own conversion
  // graph, so the UI cannot propose a target that would be rejected.
  const targets = useMemo(() => workflowTargetOptions(allTargets), [allTargets]);

  useEffect(() => {
    if (!open) return;
    setName(workflow?.name ?? "");
    setDescription(workflow?.description ?? "");
    setTarget(workflow?.definition.operations[0]?.target_format ?? "pdf");
    setFormError(null);
  }, [open, workflow]);

  async function save() {
    if (busy) return;
    setBusy(true);
    setFormError(null);
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      definition: { operations: [{ type: "convert" as const, target_format: target }] },
    };
    try {
      const saved = workflow
        ? await client.updateWorkflow(workflow.workflow_id, body)
        : await client.createWorkflow(body);
      onSaved(saved);
    } catch (err) {
      // A validation failure is the user's to fix, so it is shown in the form
      // rather than only as a toast that disappears.
      setFormError(err instanceof Error ? err.message : "Could not save this workflow");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={workflow ? "Edit workflow" : "New workflow"}
      description="Choose what the workflow does. You pick the files each time you run it."
      // `max-w-2xl` so the 520px format panel opens **inside** the dialog rather
      // than hanging past its right edge.
      maxWidth="max-w-2xl"
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        {/* The operation comes first. It is what a workflow *is* — the name and
            description are labels for it — and ordering it first also puts the
            format popover high enough in the dialog to open fully: below the
            text fields the trigger sat ~90px from the bottom edge and the
            panel's own 380px ran off the viewport, leaving the format grid
            partly unreachable. */}
        <div>
          <span className="block text-sm font-medium">Convert every selected file to</span>
          {/* The app's own format picker, not a native `<select>`: the graph has
              ~67 formats, and a plain dropdown of that many two-to-five letter
              codes is unusable — you cannot see what a format *is*, and there is
              nowhere to search. `FormatPicker` brings the category sidebar, the
              search box and the per-format colour tile that the Convert and
              guest pages already use, and `allowed` keeps it to formats the
              server will actually accept.

              Left-aligned, not centred: the panel is 520px wide and opens from
              the trigger's left edge, so a centred trigger pushed it past the
              dialog's right edge. */}
          <div className="mt-2">
            <FormatPicker
              value={target}
              onChange={setTarget}
              ariaLabel="Choose target format"
              allowed={targets}
              pending={mapLoading}
            />
          </div>
          <p className="mt-2 text-xs text-muted">
            Files that cannot be converted to this format are reported when you run it — nothing
            is converted until then.
          </p>
        </div>

        <div>
          <label htmlFor="workflow-name" className="block text-sm font-medium">
            Name
          </label>
          <input
            id="workflow-name"
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
            placeholder="Prepare application documents"
            className="mt-1 w-full rounded-lg border border-outline bg-surface-variant/40 px-3 py-2 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25"
          />
        </div>

        <div>
          <label htmlFor="workflow-description" className="block text-sm font-medium">
            Description <span className="font-normal text-muted">(optional)</span>
          </label>
          <input
            id="workflow-description"
            value={description}
            maxLength={400}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="What this workflow is for"
            className="mt-1 w-full rounded-lg border border-outline bg-surface-variant/40 px-3 py-2 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/25"
          />
        </div>

        {formError && (
          <p role="alert" className="rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error">
            {formError}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy || !name.trim()}>
            <FloppyDisk size={16} /> {busy ? "Saving…" : workflow ? "Save changes" : "Save workflow"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * Run dialog: pick files, then start.
 *
 * The file list is loaded once and filtered locally, because the server has no
 * "list files for a picker" endpoint and the drive listing is already paginated
 * for browsing. The selection is sent by id, and the server re-authorizes every
 * one of them on the run — this picker is a convenience, never the check.
 */
function RunWorkflowModal({
  workflow,
  onClose,
  onRan,
}: {
  workflow: SavedWorkflow | null;
  onClose: () => void;
  onRan: () => void;
}) {
  const { api: client } = useAuth();
  const { success, error: toastError } = useToast();

  const [files, setFiles] = useState<FileMetadataResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);

  const open = workflow !== null;
  const targets = workflow?.definition.operations.map((op) => op.target_format) ?? [];

  useEffect(() => {
    if (!open) return;
    setSelected(new Set());
    setResult(null);
    setLoading(true);
    client
      .listFiles()
      .then((response) => setFiles(response.files))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, [open, client]);

  function toggle(fileId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) next.delete(fileId);
      else next.add(fileId);
      return next;
    });
  }

  async function run() {
    if (!workflow || busy || selected.size === 0) return;
    setBusy(true);
    setResult(null);
    try {
      const response = await client.runWorkflow(workflow.workflow_id, Array.from(selected));
      setResult(response);
      if (response.created_count > 0) {
        success(
          `${response.created_count} ${response.created_count === 1 ? "conversion" : "conversions"} started.`,
        );
        onRan();
      }
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not run this workflow");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={workflow ? `Run “${workflow.name}”` : "Run workflow"}
      description={targets.length > 0 ? `Selected files will be converted to ${targets.map((t) => t.toUpperCase()).join(", ")}.` : undefined}
      maxWidth="max-w-lg"
    >
      <div className="space-y-4">
        {loading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} className="h-11 w-full rounded-lg" />
            ))}
          </div>
        ) : files.length === 0 ? (
          <p className="rounded-xl border border-outline bg-surface-variant/40 px-4 py-6 text-center text-sm text-muted">
            You have no files to convert yet. Upload one to your Drive first.
          </p>
        ) : (
          <>
            <p className="text-sm text-muted">
              {selected.size} of {files.length} selected
            </p>
            <ul className="max-h-72 space-y-1 overflow-y-auto rounded-lg border border-outline p-1">
              {files.map((file) => {
                const checked = selected.has(file.id);
                return (
                  <li key={file.id}>
                    <label
                      className={`flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-sm ${
                        checked ? "bg-primary-container" : "hover:bg-surface-variant"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(file.id)}
                        className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                      />
                      <FormatThumb
                        format={file.file_name.split(".").pop()?.toLowerCase() ?? ""}
                        size="2xs"
                        label=""
                        className="shrink-0"
                      />
                      <span className="min-w-0 flex-1 truncate" title={file.file_name}>
                        {file.file_name}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </>
        )}

        {/* Per-item outcomes: a run is a batch, so partial success is a real
            result and each failure names its own file. */}
        {result && (
          <div className="space-y-2">
            <p className="text-sm font-medium">
              {result.created_count} started
              {result.failed_count > 0 && `, ${result.failed_count} could not start`}
            </p>
            <ul className="space-y-1 text-xs">
              {result.items.map((item) => (
                <li key={item.file_id} className="flex items-start gap-2">
                  {item.job ? (
                    <CheckCircle size={14} weight="fill" className="mt-0.5 shrink-0 text-success" aria-hidden />
                  ) : (
                    <WarningCircle size={14} weight="fill" className="mt-0.5 shrink-0 text-error" aria-hidden />
                  )}
                  <span className="min-w-0 break-words">
                    <span className="font-medium">{item.file_name || item.file_id}</span>
                    {item.error ? `: ${item.error}` : " — started"}
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted">
              Follow progress on the{" "}
              <a href="/app/queue" className="text-primary hover:underline">
                Queue
              </a>{" "}
              page.
            </p>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            {result ? "Close" : "Cancel"}
          </Button>
          {!result && (
            <Button onClick={run} disabled={busy || selected.size === 0}>
              {busy ? (
                <CircleNotch size={16} className="animate-spin" />
              ) : (
                <ArrowRight size={16} weight="bold" />
              )}
              {busy ? "Starting…" : `Run on ${selected.size} ${selected.size === 1 ? "file" : "files"}`}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Kept as a named export so the page is lazy-loadable like the others. */
export default WorkflowsPage;
