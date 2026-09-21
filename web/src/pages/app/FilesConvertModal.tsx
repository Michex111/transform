import { useEffect, useMemo, useState } from "react";
import { Check, ArrowsClockwise } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { Modal } from "@/components/Modal";
import { Button, FormatChip } from "@/components/ui";
import { FormatIcon } from "@/components/FormatPicker";
import { formatExt, formatMeta } from "@/lib/format";
import type { FileMetadataResponse } from "@/api/types";

export function FilesConvertModal({
  open,
  onClose,
  file,
}: {
  open: boolean;
  onClose: () => void;
  file: FileMetadataResponse | null;
}) {
  const { api: client } = useAuth();
  const { addJob } = useJobs();
  const { success, error } = useToast();
  const [conversionMap, setConversionMap] = useState<Record<string, string[]>>({});
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);

  // Source format inferred from the file name (fall back to mime type).
  const source = useMemo(() => (file ? formatExt(file.file_name, file.mime_type) : ""), [file]);
  // Valid targets for this source, normalized to lowercase for matching.
  const allowedTargets = useMemo(
    () => (conversionMap[source] ?? []).map((t) => t.toLowerCase()),
    [conversionMap, source],
  );

  // Load the conversion map each time the modal opens.
  useEffect(() => {
    if (!open) return;
    let active = true;
    setTarget("");
    client
      .conversionMap()
      .then((res) => active && setConversionMap(res.conversions))
      .catch(() => active && setConversionMap({}));
    return () => {
      active = false;
    };
  }, [open, client]);

  // Default the target to the first valid format for the source.
  useEffect(() => {
    if (!target && allowedTargets.length) setTarget(allowedTargets[0]);
  }, [target, allowedTargets]);

  async function submit() {
    if (!file || !target || busy) return;
    setBusy(true);
    try {
      const job = await client.convertLibraryFile(file.id, target);
      addJob({
        ...job,
        fileName: file.file_name,
        status: job.status,
        createdAt: new Date().toISOString(),
      });
      success(`Conversion started — ${file.file_name} → ${target.toUpperCase()}.`);
      onClose();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start conversion");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Convert file"
      description={file?.file_name}
      maxWidth="max-w-lg"
    >
      <div className="space-y-4">
        {/* Source format */}
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted">From</span>
          <FormatChip format={source} />
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
                    onClick={() => setTarget(t)}
                    aria-pressed={selected}
                    className={`relative flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 transition-colors ${
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

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!target || busy}>
            <ArrowsClockwise size={16} /> {busy ? "Starting…" : "Convert"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
