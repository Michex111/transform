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
 * chosen target format in one go. Dispatches a conversion job per file and adds
 * each to the jobs queue, reporting aggregate success and per-file errors.
 *
 * The modal owns the conversion loop; the parent's `onConverted` callback only
 * needs to finish up (clear selection, exit selection mode) — it must NOT
 * re-submit jobs, or every file would be converted twice.
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
  const { success, error } = useToast();
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ ok: number; failed: number } | null>(null);

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
  }, [open]);

  useEffect(() => {
    if (!target && allowedTargets.length) setTarget(allowedTargets[0]);
  }, [target, allowedTargets]);

  async function submit() {
    if (!target || busy || files.length === 0) return;
    setBusy(true);
    setDone(null);

    let ok = 0;
    let failed = 0;
    // Convert sequentially so we can report progress per file without hammering
    // the job endpoint with parallel requests.
    for (const file of files) {
      try {
        const job = await client.convertLibraryFile(file.id, target);
        addJob({
          ...job,
          fileName: file.file_name,
          status: job.status,
          createdAt: new Date().toISOString(),
        });
        ok += 1;
      } catch (err) {
        failed += 1;
        error(`${file.file_name}: ${err instanceof Error ? err.message : "conversion failed"}`);
      }
    }

    setBusy(false);
    setDone({ ok, failed });
    if (failed === 0) {
      success(`${ok} ${ok === 1 ? "file" : "files"} → ${target.toUpperCase()} added to the queue.`);
      onConverted();
      onClose();
    } else if (ok > 0) {
      success(`${ok} converted, ${failed} failed.`);
      onConverted();
      onClose();
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
              className="flex items-center gap-2 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error"
            >
              <X size={15} weight="bold" />
              {done.failed} {done.failed === 1 ? "file" : "files"} failed — everything else was queued.
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
      </div>
    </Modal>
  );
}
