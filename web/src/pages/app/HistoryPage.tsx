import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download, ArrowCounterClockwise } from "@phosphor-icons/react";
import { useJobs } from "@/jobs/JobsContext";
import { useAuth } from "@/auth/AuthContext";
import { Card, FormatChip, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

const FILTERS = ["pdf", "docx", "xlsx", "png", "mp3", "mp4"] as const;

export function HistoryPage() {
  const { jobs } = useJobs();
  const { api: client } = useAuth();
  const [format, setFormat] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("all");

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
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="ml-auto h-9 rounded-lg border border-outline-strong bg-surface-variant px-2 text-sm text-on-background focus:border-primary focus:outline-none"
          aria-label="Filter by status"
        >
          <option value="all">All statuses</option>
          <option value="COMPLETED">Ready</option>
          <option value="PROCESSING">Converting</option>
          <option value="PENDING">Queued</option>
          <option value="FAILED">Failed</option>
        </select>
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
                    <a
                      href={client.getJobDownloadUrl(job.job_id)}
                      className="text-muted hover:text-primary"
                      aria-label="Download"
                    >
                      <Download size={18} />
                    </a>
                  )}
                  {job.status === "FAILED" && (
                    <Link
                      to="/app/convert"
                      className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                    >
                      <ArrowCounterClockwise size={14} /> Retry
                    </Link>
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
