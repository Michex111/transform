import { useEffect, useMemo, useState } from "react";
import { ArrowsClockwise, Check, CircleNotch, FileText, X } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { Modal } from "@/components/Modal";
import { Button, FormatChip } from "@/components/ui";
import { FormatIcon } from "@/components/FormatPicker";
import { formatMeta } from "@/lib/format";
import { useConversionMap } from "@/lib/useConversionMap";
import { motion, AnimatePresence } from "motion/react";
import type { FileMetadataResponse } from "@/api/types";

/**
 * Convert a group of selected files (all sharing the same source format) into a
 * chosen target format in one go.
 *
 * This dispatches through `POST /conversions/batch` — **one request** — rather
 * than looping over the single-conversion endpoint. The loop was the old
 * implementation, and it was wrong in three ways that a batch endpoint fixes:
 *
 *   - it issued N requests, so a five-file selection was five round trips and
 *     five chances to trip the rate limiter;
 *   - it gave the frontend the job of deciding which files may be converted,
 *     where the only authority is the server's own ownership check; and
 *   - it had no group identity, so a failed file could not be retried *as part
 *     of the same batch* and the set was unrecoverable after a reload.
 *
 * The response is per item, so a partially-failed selection is reported
 * honestly: the items that started stay started, and each failure names its own
 * file. The batch id is kept so the run can be re-read later.
 *
 * The parent's `onConverted` callback only finishes up (clear selection, exit
 * selection mode) — it must NOT re-submit jobs, or every file would be
 * converted twice.
 */
export function FilesMassConvertModal({
  open,
  onClose,
  files,
  sourceFormat,
  onConverted,
}: {
  open: boolean;
  onClose: () => void;
  files: FileMetadataResponse[];
  sourceFormat: string;
  onConverted: () => void;
}) {
  const { api: client } = useAuth();
  const { addJob } = useJobs();
  const { success, error: toastError } = useToast();
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ ok: number; failed: number } | null>(null);
  // The per-file reasons a batch reported. Kept so the modal can name the files
  // that did not start instead of only counting them.
  const [itemErrors, setItemErrors] = useState<Array<{ name: string; reason: string }>>([]);

  const source = sourceFormat.toLowerCase();
  // Valid targets come from the shared, cached conversion map; `enabled` keeps a
  // closed modal from requesting it at all.
  const { targetsFor } = useConversionMap({ enabled: open });
  const allowedTargets = useMemo(
    () => targetsFor(source).map((t) => t.toLowerCase()),
    [targetsFor, source],
  );

  useEffect(() => {
    if (!open) return;
    setTarget("");
    setDone(null);
    setItemErrors([]);
  }, [open]);

  useEffect(() => {
    if (!target && allowedTargets.length) setTarget(allowedTargets[0]);
  }, [target, allowedTargets]);

  async function submit() {
    if (!target || busy || files.length === 0) return;
    setBusy(true);
    setDone(null);
    setItemErrors([]);

    try {
      const result = await client.batchConvert(
        files.map((file) => file.id),
        target,
      );

      // Register only the jobs that actually started. A failed item has no job,
      // so nothing is added to the queue for it.
      for (const item of result.items) {
        if (!item.job) continue;
        const file = files.find((candidate) => candidate.id === item.file_id);
        addJob({
          ...item.job,
          fileName: item.file_name || file?.file_name || item.job.input_file,
          status: item.job.status,
          createdAt: new Date().toISOString(),
        });
      }

      const failures = result.items
        .filter((item) => item.error)
        .map((item) => ({ name: item.file_name || item.file_id, reason: item.error ?? "" }));

      setItemErrors(failures);
      setDone({ ok: result.created_count, failed: result.failed_count });

      if (result.failed_count === 0) {
        success(
          `${result.created_count} ${result.created_count === 1 ? "file" : "files"} → ${target.toUpperCase()} added to the queue.`,
        );
        onConverted();
        onClose();
      } else if (result.created_count > 0) {
        // Stays open so the user can read which files failed and why.
        success(`${result.created_count} queued, ${result.failed_count} could not start.`);
        onConverted();
      }
      // Nothing started: leave the modal open on the error list rather than
      // closing over a message the user would have to catch in a toast.
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not start the conversions");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Convert files"
      description={`${files.length} selected • all ${source.toUpperCase()}`}
      maxWidth="max-w-lg"
    >
      <div className="space-y-4">
        {/* Source format summary */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted">From</span>
          <FormatChip format={source} />
          {files.length > 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted">
              <FileText size={13} /> {files.length} {files.length === 1 ? "file" : "files"}
            </span>
          )}
        </div>

        {allowedTargets.length > 0 ? (
          <>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted">Convert to</span>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {allowedTargets.map((t) => {
                const selected = t === target;
                const meta = formatMeta(t);
                return (
                  <button
                    key={t}
                    type="button"
                    disabled={busy}
                    onClick={() => setTarget(t)}
                    aria-pressed={selected}
                    className={`relative flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 transition-colors disabled:opacity-60 ${
                      selected
                        ? "border-primary bg-primary-container"
                        : "border-outline bg-surface-variant/40 hover:border-primary/50"
                    }`}
                  >
                    <FormatIcon ext={t} />
                    <span
                      className="font-mono text-xs font-semibold uppercase"
                      style={{ color: meta.color }}
                    >
                      {meta.label}
                    </span>
                    {selected && (
                      <span className="absolute right-1.5 top-1.5 text-primary">
                        <Check size={14} weight="bold" />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <p className="rounded-xl border border-outline bg-surface-variant/40 px-4 py-6 text-center text-sm text-muted">
            No conversions are available for <span className="font-mono">{source.toUpperCase()}</span>.
          </p>
        )}

        {/* Per-file progress / errors */}
        <AnimatePresence initial={false}>
          {busy && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <div className="flex items-center gap-2 rounded-lg border border-outline bg-surface-variant/40 px-3 py-2 text-sm text-muted">
                <CircleNotch size={15} className="animate-spin text-primary" />
                Converting {files.length} {files.length === 1 ? "file" : "files"}…
              </div>
            </motion.div>
          )}
          {done && done.failed > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="space-y-1.5 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error"
            >
              <p className="flex items-center gap-2 font-medium">
                <X size={15} weight="bold" />
                {done.failed} {done.failed === 1 ? "file" : "files"} could not start
                {done.ok > 0 ? " — everything else was queued." : "."}
              </p>
              {/* The server's own reason per file, so the user can act on the
                  ones they care about rather than re-guessing the whole set. */}
              <ul className="space-y-1 pl-6 text-xs">
                {itemErrors.map((item) => (
                  <li key={item.name} className="min-w-0 break-words">
                    <span className="font-medium">{item.name}</span>: {item.reason}
                  </li>
                ))}
              </ul>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!target || busy || files.length === 0}>
            <ArrowsClockwise size={16} /> {busy ? "Converting…" : "Convert all"}
          </Button>
        </div>
        {done && done.failed > 0 && done.ok === 0 && (
          // Nothing started, so the modal stays open on the reasons rather than
          // closing over a message the user would have to catch in a toast.
          <Button variant="secondary" className="w-full" onClick={onClose}>
            Close
          </Button>
        )}
      </div>
    </Modal>
  );
}
