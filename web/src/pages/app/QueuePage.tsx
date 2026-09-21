import { memo, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { LayoutGroup, motion } from "motion/react";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { activeJobs } from "@/jobs/jobStore";
import { Dropdown } from "@/components/Dropdown";
import { Card, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";

type SortKey = "newest" | "oldest" | "status" | "format" | "filename";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "status", label: "By status" },
  { key: "format", label: "By format" },
  { key: "filename", label: "By name" },
];

/** Sort order for "By status". Only in-progress statuses reach the queue. */
const STATUS_ORDER: Record<string, number> = {
  PROCESSING: 0,
  PENDING: 1,
  AWAITING_UPLOAD: 2,
};

interface QueueRowProps {
  job: UiJob;
}

/**
 * Memoized queue row. Keyed by stable props (job id + callback identities) so a
 * single SSE progress tick on one job does not re-render every other row.
 *
 * Only in-progress jobs are rendered here, so there are no download/retry
 * actions: finished conversions are reported by History, which owns those.
 */
const QueueRow = memo(function QueueRow({ job }: QueueRowProps) {
  return (
    <motion.li
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
      <span className="hidden items-center gap-1.5 sm:flex">
        <FormatChip format={job.source_format} size="xs" />
        <FormatChip format={job.target_format} size="xs" />
      </span>
      <StatusBadge status={job.status} />
      <div className="hidden sm:block">
        <ProgressBar
          value={job.progress ?? (job.status === "PROCESSING" ? 45 : 0)}
          from="var(--color-primary)"
        />
      </div>
      <span className="hidden justify-self-end font-mono text-xs text-muted lg:block">
        {formatDateTime(job.createdAt)}
      </span>
    </motion.li>
  );
});

export function QueuePage() {
  const { jobs, refresh } = useJobs();
  const [sort, setSort] = useState<SortKey>("newest");

  // Load the current user's jobs on mount, so the queue also reflects work
  // started elsewhere (or before a reload) rather than only this tab's.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // The queue is a live view: only work still in flight belongs here. A
  // finished conversion moves to History, which reports its outcome and cost.
  const active = useMemo(() => activeJobs(jobs), [jobs]);

  const sorted = useMemo(() => {
    const arr = [...active];
    switch (sort) {
      case "newest":
        return arr.sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? ""));
      case "oldest":
        return arr.sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? ""));
      case "status":
        return arr.sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
      case "format":
        return arr.sort((a, b) => a.source_format.localeCompare(b.source_format));
      case "filename":
        // The backend does not return file size, so sort by filename instead
        // of the misleading "size" heuristic (which sorted by name length).
        return arr.sort((a, b) =>
          (a.fileName ?? a.input_file).localeCompare(b.fileName ?? b.input_file),
        );
    }
  }, [active, sort]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Queue</h1>
        <p className="text-sm text-muted">Conversions running right now.</p>
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
        <span className="font-mono text-xs text-muted">{active.length} active</span>
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
            Nothing is running right now. Finished conversions appear in{" "}
            <Link to="/app/history" className="font-medium text-primary hover:underline">
              History
            </Link>
            .
          </p>
        ) : (
          <ul className="divide-y divide-outline">
            <LayoutGroup>
              {sorted.map((job) => (
                <QueueRow key={job.job_id} job={job} />
              ))}
            </LayoutGroup>
          </ul>
        )}
      </Card>
    </div>
  );
}
