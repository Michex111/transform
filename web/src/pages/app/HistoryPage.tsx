import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Download, ArrowCounterClockwise } from "@phosphor-icons/react";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { getCachedFile, dropCachedFile } from "@/lib/fileCache";
import { Dropdown } from "@/components/Dropdown";
import { ErrorButton } from "@/components/ErrorButton";
import { Card, FormatChip, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const FILTERS = ["pdf", "docx", "xlsx", "png", "mp3", "mp4"] as const;

export function HistoryPage() {
  const { jobs, updateJob, refresh } = useJobs();
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const [format, setFormat] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("all");

  // Load persisted history from the server on mount.
  useEffect(() => {
    refresh();
  }, [refresh]);

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

  const filtered = useMemo(() => {
    return jobs.filter((j) => {
      if (format && j.source_format !== format && j.target_format !== format) return false;
      if (status !== "all" && j.status !== status) return false;
      return true;
    });
  }, [jobs, format, status]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">History</h1>
        <p className="text-sm text-muted">Everything you've converted.</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFormat(format === f ? null : f)}
              className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                format === f
                  ? "border-primary bg-primary-container text-on-primary-container"
                  : "border-outline-strong text-muted hover:bg-surface-variant"
              }`}
            >
              {f.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <Dropdown
            value={status}
            onChange={setStatus}
            ariaLabel="Filter by status"
            align="right"
            options={[
              { value: "all", label: "All statuses" },
              { value: "COMPLETED", label: "Ready" },
              { value: "PROCESSING", label: "Converting" },
              { value: "PENDING", label: "Queued" },
              { value: "FAILED", label: "Failed" },
            ]}
          />
        </div>
      </div>

      {/* Table */}
      <Card className="overflow-hidden">
        <div className="hidden grid-cols-[2fr_1fr_1fr_auto] gap-4 border-b border-outline bg-surface-variant/40 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted md:grid">
          <span>File</span>
          <span>Format</span>
          <span>Status</span>
          <span className="text-right">Created</span>
        </div>

        {filtered.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <p className="font-display text-lg font-semibold">Nothing here yet</p>
            <p className="mt-1 text-sm text-muted">Completed and failed conversions will show up here.</p>
            <Link to="/app/convert" className="mt-4 inline-block text-sm font-medium text-primary hover:underline">
              Convert a file
            </Link>
          </div>
        ) : (
          <ul className="divide-y divide-outline">
            {filtered.map((job) => (
              <li
                key={job.job_id}
                className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50 md:grid-cols-[2fr_1fr_1fr_auto]"
              >
                <span className="min-w-0 truncate text-sm text-on-background">
                  {job.fileName ?? job.input_file}
                </span>
                <span className="hidden items-center gap-1 md:flex">
                  <FormatChip format={job.source_format} />
                  <FormatChip format={job.target_format} />
                </span>
                <StatusBadge status={job.status} />
                <div className="flex items-center justify-end gap-3">
                  <span className="hidden font-mono text-xs text-muted lg:block">
                    {formatDateTime(job.createdAt)}
                  </span>
                  {job.status === "COMPLETED" && (
                    <button
                      onClick={() => handleDownload(job.job_id)}
                      className="text-muted transition-transform hover:scale-110 hover:text-primary"
                      aria-label="Download"
                    >
                      <Download size={18} />
                    </button>
                  )}
                  {job.status === "FAILED" && (
                    <>
                      <ErrorButton message={job.errorMessage} />
                      <button
                        onClick={() => handleRetry(job)}
                        className="inline-flex items-center gap-1 text-xs font-medium text-primary transition-transform hover:scale-105 hover:underline"
                      >
                        <ArrowCounterClockwise size={14} /> Retry
                      </button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
