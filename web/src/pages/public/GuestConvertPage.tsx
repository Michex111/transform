import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, DownloadSimple, Swap, Trash, UploadSimple } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { FormatPicker } from "@/components/FormatPicker";
import { Button, Card, FormatMorph, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";
import { formatDateTime, formatMeta } from "@/lib/format";
import { friendlyErrorMessage } from "@/lib/errorMessages";
import { useGuestHistory } from "@/lib/useGuestHistory";
import type { GuestHistoryItem } from "@/api/types";

const MAX_BYTES = 100 * 1024 * 1024; // 100 MB

export function GuestConvertPage() {
  const { api: client, isAuthenticated } = useAuth();
  const { success, error } = useToast();
  // The hook owns the SSE lifecycle for in-progress guest jobs; it wires the
  // guest subscribe + job-refresh helpers to the API client so removing or
  // clearing history also aborts any dangling streams.
  const subscribe = useMemo(
    () => Object.assign(
      (item: GuestHistoryItem, handlers: Parameters<typeof client.guestSubscribeToJob>[2]) =>
        client.guestSubscribeToJob(item.job_id, item.guest_token, handlers),
      {
        refresh: async (item: GuestHistoryItem): Promise<Partial<GuestHistoryItem> | null> => {
          const job = await client.guestGetJob(item.job_id, item.guest_token);
          return {
            status: job.status,
            output_file: job.output_file,
            download_url: job.download_url,
            input_file: job.input_file,
            progress: job.status === "COMPLETED" ? 100 : undefined,
          };
        },
      },
    ),
    // `client` is a stable singleton on the auth context.
    [client],
  );
  const { items, addItem, removeItem, clearHistory } = useGuestHistory(subscribe);
  const fileInput = useRef<HTMLInputElement>(null);
  const reduce = useReducedMotion();

  const [from, setFrom] = useState("pdf");
  const [to, setTo] = useState("docx");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Source → valid target formats from the guest conversion map.
  const [conversionMap, setConversionMap] = useState<Record<string, string[]>>({});

  useEffect(() => {
    let active = true;
    client
      .guestConversionMap()
      .then((res) => active && setConversionMap(res.conversions))
      .catch(() => {
        /* fall back to un-restricted picker */
      });
    return () => {
      active = false;
    };
  }, [client]);

  const allowedTargets = useMemo(() => conversionMap[from] ?? [], [conversionMap, from]);
  const allowedSources = useMemo(() => Object.keys(conversionMap), [conversionMap]);

  // Keep `to` valid for the selected source, and `from` a valid source.
  useEffect(() => {
    if (allowedSources.length && !allowedSources.includes(from)) {
      setFrom(allowedSources[0]);
    }
  }, [allowedSources, from]);

  useEffect(() => {
    if (allowedTargets.length && !allowedTargets.includes(to)) {
      setTo(allowedTargets[0]);
    }
  }, [allowedTargets, to]);

  // ---- Drag & drop ----
  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDragOver(true);
  }

  function onDragEnter(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(true);
  }

  function onDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragOver(false);
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (!dropped) return;
    if (!validateFileFormat(dropped)) return;
    setFile(dropped);
  }

  /**
   * Validate that a picked/dropped file's extension matches the selected source
   * format, so a mismatch is rejected before upload instead of failing
   * mid-conversion (and leaking a server-side error like a temp path).
   */
  function validateFileFormat(file: File): boolean {
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!ext) return true;
    if (ext === from) return true;
    // Allow switching the source format to match the file if it's a valid one.
    if (allowedSources.includes(ext)) {
      setFrom(ext);
      return true;
    }
    error(`".${ext}" isn't a supported source format for this conversion.`);
    return false;
  }

  async function startConversion() {
    if (!file) {
      error("Choose a file first.");
      return;
    }
    if (file.size > MAX_BYTES) {
      error("File is too large. The maximum upload size is 100 MB.");
      return;
    }

    setBusy(true);
    try {
      // Full guest flow: encrypt (for files < 1 GB) → create job → upload
      // session → PUT bytes → verify/enqueue. The client encrypts the file to
      // a FENCR blob before upload and passes the data key; if the deployment
      // has no master key it falls back to plaintext automatically.
      const job = await client.guestConvertWithFile(from, to, file);
      const guestToken = job.guest_token;

      addItem({
        job_id: job.job_id,
        guest_token: guestToken,
        fileName: file.name,
        input_file: file.name,
        source_format: from,
        target_format: to,
        status: "PENDING",
        progress: 0,
        createdAt: new Date().toISOString(),
      });

      success("Conversion started — watch it in your history below.");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start conversion");
    } finally {
      setBusy(false);
    }
  }

  async function downloadJob(item: GuestHistoryItem) {
    try {
      // `guestDownload` is self-healing: if the stored `download_url` is stale
      // or absent (the worker emits COMPLETED before persisting the output, so
      // the UI can briefly show "Ready" without a URL yet), it re-fetches the
      // job once and retries briefly before failing.
      await client.guestDownload(item);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not download the file");
    }
  }

  return (
    <div className="format-glyph-field">
      <div className="mx-auto max-w-4xl space-y-6 px-4 py-12 sm:px-6">
        <div>
          <p className="mb-2 inline-flex items-center gap-2 rounded-full border border-outline-strong px-3 py-1 text-xs font-medium uppercase tracking-wider text-muted">
            No account needed
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Convert without an account
          </h1>
          <p className="mt-2 max-w-md text-muted">
            Move a file from one format to another. Your history stays on this device.
          </p>
        </div>

        {/* Sign-in banner for authenticated users */}
        {isAuthenticated && (
          <Card className="flex flex-col items-start gap-3 border-primary/30 p-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted">You're signed in — use the full workspace instead.</p>
            <Link to="/app/convert">
              <Button size="sm" variant="secondary">
                Open app <ArrowRight size={16} />
              </Button>
            </Link>
          </Card>
        )}

        {/* Conversion panel */}
        <Card className="space-y-6 p-6">
          <div className="flex items-center justify-center gap-6">
            <div className="flex flex-col items-center gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">From</span>
              <FormatPicker value={from} onChange={setFrom} ariaLabel="Choose source format" align="left" allowed={allowedSources} />
            </div>

            <div className="flex flex-col items-center gap-1">
              <motion.button
                onClick={() => {
                  const newFrom = to;
                  const newTo = from;
                  const targets = conversionMap[newFrom] ?? [];
                  setFrom(newFrom);
                  setTo(targets.includes(newTo) ? newTo : (targets[0] ?? newTo));
                }}
                aria-label="Swap formats"
                className="flex h-9 w-9 items-center justify-center rounded-full border border-outline-strong bg-surface text-muted transition-colors hover:text-primary"
                whileHover={reduce ? undefined : { rotate: 180 }}
                transition={{ type: "spring", stiffness: 260, damping: 18 }}
              >
                <Swap size={16} />
              </motion.button>
              <span className="text-[10px] uppercase tracking-wide text-muted">To</span>
            </div>

            <div className="flex flex-col items-center gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">To</span>
              <FormatPicker value={to} onChange={setTo} ariaLabel="Choose target format" align="right" allowed={allowedTargets} />
            </div>
          </div>

          <div className="flex justify-center">
            <FormatMorph from={from} to={to} animated size="lg" />
          </div>

          {/* Drop zone */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileInput.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileInput.current?.click();
              }
            }}
            onDragOver={onDragOver}
            onDragEnter={onDragEnter}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={`flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-10 text-center transition-colors ${
              dragOver
                ? "border-primary bg-primary-container/30"
                : "border-outline-strong bg-surface-variant/40 hover:border-primary"
            }`}
          >
            <motion.span
              animate={reduce ? undefined : { y: [0, -4, 0] }}
              transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
              className="text-muted"
            >
              <UploadSimple size={28} />
            </motion.span>
            <span className="text-sm font-medium text-on-background">
              {file ? file.name : "Drop your file here or browse"}
            </span>
            {!file && (
              <span className="font-mono text-xs text-muted">
                .{from} · max 100 MB
              </span>
            )}
            <input
              ref={fileInput}
              type="file"
              accept={`.${from}`}
              className="hidden"
              onChange={(e) => {
                const picked = e.target.files?.[0] ?? null;
                if (picked && !validateFileFormat(picked)) {
                  // Reject mismatched files before upload; clear the input so a
                  // corrected re-pick triggers a fresh change event.
                  e.target.value = "";
                  return;
                }
                setFile(picked);
              }}
            />
          </div>

          {/* Submit */}
          <div className="flex flex-col items-center gap-2">
            <Button size="lg" onClick={startConversion} disabled={busy}>
              {busy ? "Starting…" : "Start conversion"}
            </Button>
            <p className="text-xs text-muted">You'll see it in your history below.</p>
          </div>
        </Card>

        {/* Guest history */}
        <Card className="overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-outline px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="font-display text-lg font-semibold">Your conversions</h2>
            {items.length > 0 && (
              <button
                onClick={() => {
                  clearHistory();
                  success("History cleared.");
                }}
                className="inline-flex items-center gap-1 text-sm font-medium text-muted hover:text-error"
              >
                <Trash size={16} /> Clear history
              </button>
            )}
          </div>

          {items.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-5 py-12 text-center">
              <FormatMorph from="pdf" to="docx" size="md" animated />
              <p className="text-sm text-muted">No conversions yet. Try one above.</p>
            </div>
          ) : (
            <ul className="divide-y divide-outline">
              {items.map((job) => (
                <li key={job.job_id} className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-3 sm:grid-cols-[1fr_auto_160px]">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-on-background">{job.fileName}</p>
                    <div className="mt-1 flex items-center gap-2">
                      <FormatChip format={job.source_format} />
                      <ArrowRight size={12} className="text-muted" />
                      <FormatChip format={job.target_format} />
                      <span className="font-mono text-[10px] text-muted">{formatDateTime(job.createdAt)}</span>
                    </div>
                    {job.errorMessage && (
                      <p className="mt-1 truncate text-xs text-error">{friendlyErrorMessage(job.errorMessage)}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="hidden w-32 sm:block">
                      <ProgressBar
                        value={
                          job.progress ??
                          (job.status === "COMPLETED" ? 100 : job.status === "PROCESSING" ? 45 : 0)
                        }
                        from={formatMeta(job.source_format).color}
                        to={formatMeta(job.target_format).color}
                      />
                    </div>
                    <StatusBadge status={job.status} />
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    {job.status === "COMPLETED" && (
                      <Button size="sm" variant="secondary" onClick={() => downloadJob(job)}>
                        <DownloadSimple size={16} /> Download
                      </Button>
                    )}
                    <button
                      onClick={() => {
                        removeItem(job.job_id);
                        success("Conversion removed from history.");
                      }}
                      aria-label="Remove from history"
                      className="flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-variant hover:text-error"
                    >
                      <Trash size={15} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
