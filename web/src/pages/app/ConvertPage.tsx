import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, UploadSimple, Swap } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { cacheFileForJob } from "@/lib/fileCache";
import { FormatPicker } from "@/components/FormatPicker";
import { Button, Card, FormatMorph, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";

export function ConvertPage() {
  const { api: client } = useAuth();
  const { addJob } = useJobs();
  const { success, error } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const prefill = (location.state as { source?: string; target?: string } | null) ?? {};

  const [from, setFrom] = useState(prefill.source ?? "pdf");
  const [to, setTo] = useState(prefill.target ?? "docx");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const { jobs } = useJobs();

  // Source → valid target formats from the backend conversion map.
  const [conversionMap, setConversionMap] = useState<Record<string, string[]>>({});

  useEffect(() => {
    let active = true;
    client
      .conversionMap()
      .then((res) => active && setConversionMap(res.conversions))
      .catch(() => {
        /* fall back to un-restricted picker */
      });
    return () => {
      active = false;
    };
  }, [client]);

  // The target formats currently allowed for the chosen source.
  const allowedTargets = useMemo(() => conversionMap[from] ?? [], [conversionMap, from]);
  // The source formats that have at least one valid target.
  const allowedSources = useMemo(() => Object.keys(conversionMap), [conversionMap]);

  // Keep `to` valid for the selected source, and keep `from` a valid source.
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

  const inlineQueue = useMemo(() => jobs.filter((j) => j.status !== "COMPLETED" && j.status !== "FAILED").slice(0, 4), [jobs]);

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
    // Only clear when leaving the zone itself (not a child).
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragOver(false);
  }

  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (!dropped) return;
    // Infer the source format from the file extension and switch to it if it's
    // a valid source, so the job is created with the correct source format.
    const ext = dropped.name.split(".").pop()?.toLowerCase();
    if (ext && allowedSources.includes(ext)) {
      setFrom(ext);
    } else if (ext) {
      error(`".${ext}" isn't a supported source format.`);
      return;
    }
    setFile(dropped);
  }

  async function startConversion() {
    if (!file) {
      error("Choose a file first.");
      return;
    }

    setBusy(true);
    try {
      // Run the full conversion flow: create job, upload file, verify/enqueue.
      const job = await client.convertWithFile(from, to, file);

      // Cache the file against the job id so a later retry can re-upload it
      // without the user re-selecting the file.
      cacheFileForJob(job.job_id, { file, source: from, target: to });

      addJob({
        ...job,
        fileName: file.name,
        status: "PENDING",
        createdAt: new Date().toISOString(),
      });
      success("Conversion started — it's in the queue.");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start conversion");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Convert</h1>
        <p className="text-sm text-muted">Move a file from one format to another.</p>
      </div>

      {/* Conversion panel */}
      <Card className="space-y-6 p-6">
        {/* Format picker: source → target with a swap control */}
        <div className="flex items-center justify-center gap-6">
          <div className="flex flex-col items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">From</span>
            <FormatPicker value={from} onChange={setFrom} ariaLabel="Choose source format" align="left" allowed={allowedSources} />
          </div>

          <div className="flex flex-col items-center gap-1">
            <motion.button
              onClick={() => {
                // Swap, then ensure the new target is valid for the new source.
                const newFrom = to;
                const newTo = from;
                const targets = conversionMap[newFrom] ?? [];
                setFrom(newFrom);
                setTo(targets.includes(newTo) ? newTo : (targets[0] ?? newTo));
              }}
              aria-label="Swap formats"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-outline-strong bg-surface text-muted transition-colors hover:text-primary"
              whileHover={{ rotate: 180 }}
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
            animate={{ y: [0, -4, 0] }}
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
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        {/* Submit */}
        <div className="flex flex-col items-center gap-2">
          <Button size="lg" onClick={startConversion} disabled={busy}>
            {busy ? "Starting…" : "Start conversion"}
          </Button>
          <p className="text-xs text-muted">You'll see it in the queue below.</p>
        </div>
      </Card>

      {/* Inline queue */}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Queue</h2>
          <Link to="/app/queue" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
            View full queue <ArrowRight size={16} />
          </Link>
        </div>
        {inlineQueue.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-muted">
            Nothing in the queue right now.
          </p>
        ) : (
          <ul className="divide-y divide-outline">
            {inlineQueue.map((job) => (
              <li key={job.job_id} className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-3 sm:grid-cols-[1fr_auto_140px]">
                <div className="min-w-0">
                  <p className="truncate text-sm text-on-background">{job.fileName ?? job.input_file}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <FormatChip format={job.source_format} />
                    <ArrowRight size={12} className="text-muted" />
                    <FormatChip format={job.target_format} />
                  </div>
                </div>
                <StatusBadge status={job.status} />
                <div className="hidden sm:block">
                  <ProgressBar
                    value={job.progress ?? (job.status === "COMPLETED" ? 100 : job.status === "PROCESSING" ? 45 : 0)}
                    from="var(--color-primary)"
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
