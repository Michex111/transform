import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import { ArrowRight, UploadSimple, Swap } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs } from "@/jobs/JobsContext";
import { cacheFileForJob } from "@/lib/fileCache";
import { Button, Card, FormatMorph, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";

export function ConvertPage() {
  const { api: client } = useAuth();
  const { addJob } = useJobs();
  const { success, error } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const prefill = (location.state as { source?: string; target?: string } | null) ?? {};

  const [formats, setFormats] = useState<string[]>(["pdf", "docx", "xlsx", "png", "jpg", "mp3", "mp4", "txt"]);
  const [from, setFrom] = useState(prefill.source ?? "pdf");
  const [to, setTo] = useState(prefill.target ?? "docx");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const { jobs } = useJobs();

  useEffect(() => {
    let active = true;
    api
      .supportedConversions()
      .then((list) => {
        if (!active || !list.length) return;
        const fmts = Array.from(new Set(list.flatMap((c) => [c.source_format, c.target_format])));
        if (fmts.length) setFormats(fmts);
      })
      .catch(() => {
        /* fall back to defaults */
      });
    return () => {
      active = false;
    };
  }, []);

  const inlineQueue = useMemo(() => jobs.filter((j) => j.status !== "COMPLETED" && j.status !== "FAILED").slice(0, 4), [jobs]);

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
        {/* Format morph selector */}
        <div className="flex items-center justify-center gap-3">
          <label className="text-sm font-medium text-muted">From</label>
          <select
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="h-10 rounded-lg border border-outline-strong bg-surface-variant px-3 font-mono text-sm font-semibold text-on-background focus:border-primary focus:outline-none"
          >
            {formats.map((f) => (
              <option key={f} value={f}>
                {f.toUpperCase()}
              </option>
            ))}
          </select>

          <FormatMorph from={from} to={to} animated />

          <label className="text-sm font-medium text-muted">To</label>
          <select
            value={to}
            onChange={(e) => setTo(e.target.value)}
            className="h-10 rounded-lg border border-outline-strong bg-surface-variant px-3 font-mono text-sm font-semibold text-on-background focus:border-primary focus:outline-none"
          >
            {formats.map((f) => (
              <option key={f} value={f}>
                {f.toUpperCase()}
              </option>
            ))}
          </select>

          <motion.button
            onClick={() => {
              setFrom(to);
              setTo(from);
            }}
            aria-label="Swap formats"
            className="text-muted transition-colors hover:text-primary"
            whileHover={{ rotate: 180 }}
            transition={{ type: "spring", stiffness: 260, damping: 18 }}
          >
            <Swap size={18} />
          </motion.button>
        </div>

        {/* Drop zone */}
        <motion.button
          type="button"
          onClick={() => fileInput.current?.click()}
          className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-outline-strong bg-surface-variant/40 p-10 text-center transition-colors hover:border-primary"
          whileHover={{ scale: 1.01 }}
          whileTap={{ scale: 0.99 }}
          transition={{ type: "spring", stiffness: 300, damping: 20 }}
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
        </motion.button>

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
