import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LayoutGroup, motion } from "motion/react";
import { Download, ArrowCounterClockwise } from "@phosphor-icons/react";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { getCachedFile, dropCachedFile } from "@/lib/fileCache";
import { Dropdown } from "@/components/Dropdown";
import { ErrorButton } from "@/components/ErrorButton";
import { Card, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

type SortKey = "newest" | "oldest" | "status" | "format" | "size";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "status", label: "By status" },
  { key: "format", label: "By format" },
  { key: "size", label: "By size" },
];

const STATUS_ORDER: Record<string, number> = {
  PROCESSING: 0,
  PENDING: 1,
  AWAITING_UPLOAD: 2,
  COMPLETED: 3,
  FAILED: 4,
};

export function QueuePage() {
  const { jobs, updateJob } = useJobs();
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const [sort, setSort] = useState<SortKey>("newest");

  async function handleDownload(jobId: string) {
    try {
      await client.downloadConvertedFile(jobId);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not download file");
    }
  }

  async function handleRetry(job: UiJob) {
    // 1. If the input object is gone from storage, fall back to a full
    //    re-upload via the normal conversion route.
    const exists = await client.objectExists(job.object_key || job.input_file);
    if (!exists) {
      const cached = getCachedFile(job.job_id);
      if (cached) {
        try {
          const newJob = await client.convertWithFile(cached.source, cached.target, cached.file);
          updateJob(job.job_id, {
            ...newJob,
            fileName: cached.file.name,
            status: "PENDING",
            progress: 0,
            createdAt: new Date().toISOString(),
          });
          dropCachedFile(job.job_id);
          success("Input file was missing — re-uploaded and re-queued.");
        } catch (err) {
          error(err instanceof Error ? err.message : "Could not re-upload file");
        }
        return;
      }
      // No cached file — send the user to Convert pre-filled.
      navigate("/app/convert", {
        state: { source: job.source_format, target: job.target_format },
      });
      error("The input file is no longer in storage. Re-select it to convert.");
      return;
    }

    // 2. Input still exists — re-enqueue server-side without re-uploading.
    try {
      const updated = await client.retryJob(job.job_id);
      updateJob(job.job_id, { status: "PENDING", progress: 0, output_file: updated.output_file, download_url: updated.download_url });
      success("Conversion re-queued — tracking it now.");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not retry conversion");
    }
  }

  const active = jobs.filter((j) => j.status === "PROCESSING" || j.status === "PENDING").length;

  const sorted = useMemo(() => {
    const arr = [...jobs];
    switch (sort) {
      case "newest":
        return arr.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
      case "oldest":
        return arr.sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""));
      case "status":
        return arr.sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
      case "format":
        return arr.sort((a, b) => a.source_format.localeCompare(b.source_format));
      case "size":
        return arr.sort((a, b) => a.input_file.length - b.input_file.length);
    }
  }, [jobs, sort]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Queue</h1>
        <p className="text-sm text-muted">Every conversion, in order.</p>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Dropdown
            label="Sort"
            value={sort}
            onChange={setSort}
            options={SORTS.map((s) => ({ value: s.key, label: s.label }))}
            ariaLabel="Sort conversions"
            align="left"
          />
        </div>
        <span className="font-mono text-xs text-muted">{active} active</span>
      </div>

      {/* Table */}
      <Card className="overflow-hidden">
        <div className="hidden grid-cols-[2fr_1fr_1fr_140px_auto] gap-4 border-b border-outline bg-surface-variant/40 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted sm:grid">
          <span>File</span>
          <span>Format</span>
          <span>Status</span>
          <span>Progress</span>
          <span className="text-right">Created</span>
        </div>

        {sorted.length === 0 ? (
          <p className="px-5 py-16 text-center text-muted">
            No conversions yet. Start one from the Convert screen.
          </p>
        ) : (
          <ul className="divide-y divide-outline">
            <LayoutGroup>
              {sorted.map((job) => (
                <motion.li
                  key={job.job_id}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ type: "spring", stiffness: 260, damping: 28 }}
                  className="grid grid-cols-[2fr_1fr_auto] items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50 sm:grid-cols-[2fr_1fr_1fr_140px_auto]"
                >
                  <span className="min-w-0 truncate text-sm text-on-background">
                    {job.fileName ?? job.input_file}
                  </span>
                  <span className="hidden items-center gap-1 sm:flex">
                    <FormatChip format={job.source_format} />
                    <FormatChip format={job.target_format} />
                  </span>
                  <StatusBadge status={job.status} />
                  <div className="hidden sm:block">
                    <ProgressBar
                      value={
                        job.progress ??
                        (job.status === "COMPLETED"
                          ? 100
                          : job.status === "PROCESSING"
                            ? 45
                            : job.status === "FAILED"
                              ? 100
                              : 0)
                      }
                      from="var(--color-primary)"
                      to={job.status === "FAILED" ? "var(--color-error)" : undefined}
                    />
                  </div>
                  <div className="flex items-center justify-end gap-3">
                    <span className="hidden font-mono text-xs text-muted lg:block">
                      {formatDateTime(job.createdAt)}
                    </span>
                    {job.status === "COMPLETED" && (
                      <button
                        onClick={() => handleDownload(job.job_id)}
                        className="text-muted transition-transform hover:scale-110 hover:text-primary"
                        aria-label="Download"
                        title="Download"
                      >
                        <Download size={18} />
                      </button>
                    )}
                    {job.status === "FAILED" && (
                      <>
                        <ErrorButton message={job.errorMessage} />
                        <motion.button
                          onClick={() => handleRetry(job)}
                          whileHover={{ rotate: -180 }}
                          transition={{ type: "spring", stiffness: 200, damping: 15 }}
                          className="text-muted hover:text-primary"
                          aria-label="Retry"
                          title="Retry"
                        >
                          <ArrowCounterClockwise size={18} />
                        </motion.button>
                      </>
                    )}
                  </div>
                </motion.li>
            ))}
            </LayoutGroup>
          </ul>
        )}
      </Card>
    </div>
  );
}
