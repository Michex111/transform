import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "motion/react";
import { Download, ArrowCounterClockwise, CaretDown, Trash } from "@phosphor-icons/react";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { showsCreditsUsed, jobCreatedAt } from "@/jobs/jobStore";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { getCachedFile, dropCachedFile } from "@/lib/fileCache";
import { Dropdown } from "@/components/Dropdown";
import { ErrorButton } from "@/components/ErrorButton";
import { JobDetailsPanel } from "@/components/JobDetailsPanel";
import { Modal } from "@/components/Modal";
import { Button, Card, CreditsBadge, FormatChip, StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import {
  readStoredStatus,
  readStoredTimeline,
  refreshRangeFor,
  timelineForNavigation,
  timelineFromNavigationState,
  writeStoredStatus,
  writeStoredTimeline,
  type StatusFilter,
  type TimelineFilter,
} from "@/lib/historyFilters";
import { HISTORY_HEADER_GRID, HISTORY_ROW_GRID } from "@/lib/tableColumns";
import { useNarrowViewport } from "@/lib/useMediaQuery";

const PRIMARY_FORMATS = ["pdf", "docx", "xlsx", "png"] as const;
const MORE_FORMATS = ["mp3", "mp4"] as const;

interface HistoryRowProps {
  job: UiJob;
  /** Whether this is the expanded row. Exactly one row is open at a time. */
  open: boolean;
  /**
   * True below `md`, where the row is a card rather than a table row. It no
   * longer controls whether the row expands (every row does, at every width) —
   * only whether the panel has to supply the Delete/Retry actions, because the
   * desktop row has them inline.
   */
  compact: boolean;
  onToggle: (jobId: string) => void;
  onDownload: (jobId: string) => void;
  onRetry: (job: UiJob) => void;
  onDelete: (job: UiJob) => void;
}

/**
 * Memoized history row. Keyed by stable props (job id + callback identities) so
 * a single SSE progress tick on one job does not re-render every other row.
 *
 * One row, two layouts: below `md` it is a card (name · status · download),
 * from `md` up it is the five-column table row. The table row keeps the format
 * chips, the token cost, the date and the per-row actions in their own tracks;
 * the card leaves the name, the status and the download button, and moves the
 * rest into the expanded panel.
 *
 * Clicking anywhere on the row — or pressing Enter on the filename button it
 * contains — expands it, at every width.
 */
const HistoryRow = memo(function HistoryRow({
  job,
  open,
  compact,
  onToggle,
  onDownload,
  onRetry,
  onDelete,
}: HistoryRowProps) {
  const reduce = useReducedMotion();
  const fileName = job.fileName ?? job.input_file;
  const panelId = `history-details-${job.job_id}`;

  return (
    <li
      className={HISTORY_ROW_GRID}
      // The whole row is the click target; the disclosure button inside it
      // carries the semantics (`aria-expanded`/`aria-controls`) and has no
      // handler of its own, so its click bubbles here. Keyboard, assistive tech
      // and mouse all run the one toggle path instead of three that can
      // disagree. The panel's own buttons stop propagation so an action never
      // also toggles the row.
      onClick={() => onToggle(job.job_id)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        className="flex min-w-0 flex-1 items-center gap-2 text-left md:min-w-0"
      >
        <span className="min-w-0 truncate text-sm text-on-background">{fileName}</span>
        {/* The chevron sits with the name, not in the action cell, so the
            download button never moves as rows open and close. */}
        <motion.span
          aria-hidden
          className="shrink-0 text-muted"
          animate={reduce ? undefined : { rotate: open ? 180 : 0 }}
          transition={{ type: "spring", stiffness: 420, damping: 30 }}
        >
          <CaretDown size={14} weight="bold" />
        </motion.span>
      </button>
      <span className="hidden items-center gap-1.5 md:flex">
        <FormatChip format={job.source_format} size="xs" />
        <FormatChip format={job.target_format} size="xs" />
      </span>
      {/* `shrink-0` keeps the pill at its natural width on the card layout: the
          name is the item that gives way, never the status. */}
      <span className="flex shrink-0 items-center">
        <StatusBadge status={job.status} />
      </span>
      {/* Created sits in its own track: it used to share the actions cell, so
          the header's "Created" label never lined up with the dates, and the
          width changed per row with the number of action buttons. */}
      <span className="hidden font-mono text-xs text-muted lg:block">
        {formatDateTime(jobCreatedAt(job))}
      </span>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">
        {/* Cost and delete are desktop-only in the row: on a phone they live in
            the expanded panel. Both are `display:none` (not unmounted), so the
            row never states the same fact twice on screen. */}
        {showsCreditsUsed(job) && (
          <span className="hidden md:inline-flex">
            <CreditsBadge credits={job.credits_used ?? 0} />
          </span>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(job);
          }}
          className="hidden text-muted transition-transform hover:scale-110 hover:text-error md:inline-flex"
          aria-label="Delete history record"
          title="Delete"
        >
          <Trash size={18} />
        </button>
        {job.status === "COMPLETED" && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDownload(job.job_id);
            }}
            // 44px on a phone: this is the one action a completed row offers, so
            // it has to be reliably tappable; `md:h-auto` restores the 18px icon
            // button the table row uses.
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-muted transition-transform hover:text-primary active:scale-90 md:h-auto md:w-auto md:hover:scale-110"
            aria-label="Download"
          >
            <Download size={18} />
          </button>
        )}
        {job.status === "FAILED" && (
          // The desktop row keeps the popover; the card shows the message as
          // plain text in the panel instead, since a hover tooltip is
          // unreachable by touch.
          <span className="hidden md:flex md:items-center md:gap-3">
            <ErrorButton message={job.errorMessage} />
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onRetry(job);
              }}
              className="inline-flex items-center gap-1 text-xs font-medium text-primary transition-transform hover:scale-105 hover:underline"
            >
              <ArrowCounterClockwise size={14} /> Retry
            </button>
          </span>
        )}
      </div>
      <JobDetailsPanel
        open={open}
        job={job}
        id={panelId}
        compact={compact}
        // From `md` up the row already has these inline, so the panel is
        // informational only there — passing the handlers is what decides.
        onDelete={compact ? onDelete : undefined}
        onRetry={compact ? onRetry : undefined}
      />
    </li>
  );
});

export function HistoryPage() {
  const { jobs, updateJob, refresh, removeJob } = useJobs();
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [format, setFormat] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFilter>(readStoredStatus);
  // A link may ask for a specific window: the navigation's History entry always
  // asks for "all", and the Dashboard's "View all" asks for 7 days. Navigation
  // state wins because it is an explicit request; the stored preference is the
  // fallback for every arrival that carries none (direct URL, reload, back
  // button).
  //
  // Read in the initializer as well as in the effect below so the first paint is
  // already the requested window instead of flashing the stored one.
  const [range, setRange] = useState<TimelineFilter>(
    () => timelineFromNavigationState(location.state) ?? readStoredTimeline(),
  );
  const [deleteTarget, setDeleteTarget] = useState<UiJob | null>(null);
  const [showMoreFormats, setShowMoreFormats] = useState(false);
  // At most one row is expanded. An accordion keeps the list the same height
  // when you open a second row, so the content you tapped does not slide away
  // under your thumb — with several panels open at once, tapping the third row
  // pushes it down by the height of the two above it.
  const [openJobId, setOpenJobId] = useState<string | null>(null);
  // Resolved once for the page rather than once per row: a subscription per row
  // would be 100 `matchMedia` listeners for a full history.
  const compact = useNarrowViewport();

  const handleToggle = useCallback((jobId: string) => {
    setOpenJobId((prev) => (prev === jobId ? null : jobId));
  }, []);

  // Persist the status filter to localStorage so it survives a reload. Safe
  // writes (never throw) and validated reads (never produce an invalid filter).
  useEffect(() => {
    writeStoredStatus(status);
  }, [status]);

  // Apply the window a navigation asked for.
  //
  // Keyed on `location.key` rather than on the requested value: clicking
  // "History" in the navigation while already on this page must RESET the window
  // to "all", and that click can carry a state object identical to the previous
  // one. Only the navigation key changes in that case, so it is the signal that
  // a fresh request arrived.
  //
  // `location.state` is in the dependencies too, because it is the value being
  // read; it is referentially stable between navigations, so this still runs
  // only when the router actually navigates.
  //
  // The functional update form is what makes the previous value available
  // without also depending on `range` — depending on it would re-run this on
  // every dropdown change for no reason.
  useEffect(() => {
    setRange((previous) => timelineForNavigation(location.state, previous));
  }, [location.key, location.state]);

  // Fetch the window the dropdown is showing. The server owns the date cut-off
  // (`range=24h|7d|30d`), so changing the filter has to re-query — filtering
  // `jobs` here instead would only ever trim the page of history the server
  // already returned, and would silently show fewer results than requested.
  useEffect(() => {
    refresh(refreshRangeFor(range));
  }, [refresh, range]);

  // Remember the choice for the next visit. Kept separate from the query above
  // so persistence can never alter what is displayed.
  useEffect(() => {
    writeStoredTimeline(range);
  }, [range]);


  const handleDownload = useCallback(
    async (jobId: string) => {
      try {
        await client.downloadConvertedFile(jobId);
      } catch (err) {
        error(err instanceof Error ? err.message : "Could not download file");
      }
    },
    [client, error],
  );

  const handleDelete = useCallback(
    async (job: UiJob) => {
      try {
        await client.deleteHistoryJob(job.job_id);
        // Remove from the shared list (also tears down its SSE subscription).
        removeJob(job.job_id);
        setDeleteTarget(null);
        success("History record deleted");
      } catch (err) {
        setDeleteTarget(null);
        error(
          err instanceof Error
            ? err.message
            : "Could not delete history record",
        );
      }
    },
    [client, removeJob, success, error],
  );

  /**
   * Deleting is destructive, so the buttons open the confirmation modal and the
   * modal calls `handleDelete`.
   *
   * The rows used to call `handleDelete` directly while the modal rendered on
   * `deleteTarget !== null` — a condition nothing ever set, so the confirmation
   * existed but could never appear and the trash icon deleted irreversibly on
   * the first tap.
   */
  const handleDeleteRequest = useCallback((job: UiJob) => {
    setDeleteTarget(job);
  }, []);

  const handleRetry = useCallback(
    async (job: UiJob) => {
      // 1. If the input object is gone from storage, fall back to a full
      //    re-upload via the normal conversion route.
      const exists = await client.objectExists(
        job.object_key || job.input_file,
      );
      if (!exists) {
        const cached = getCachedFile(job.job_id);
        if (cached) {
          try {
            const newJob = await client.convertWithFile(
              cached.source,
              cached.target,
              cached.file,
            );
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
            error(
              err instanceof Error ? err.message : "Could not re-upload file",
            );
          }
          return;
        }
        // No cached file — send the user to Convert pre-filled.
        navigate("/app/convert", {
          state: { source: job.source_format, target: job.target_format },
        });
        error(
          "The input file is no longer in storage. Re-select it to convert.",
        );
        return;
      }

      // 2. Input still exists — re-enqueue server-side without re-uploading.
      try {
        const updated = await client.retryJob(job.job_id);
        updateJob(job.job_id, {
          status: "PENDING",
          progress: 0,
          output_file: updated.output_file,
          download_url: updated.download_url,
        });
        success("Conversion re-queued — tracking it now.");
      } catch (err) {
        error(
          err instanceof Error ? err.message : "Could not retry conversion",
        );
      }
    },
    [client, navigate, updateJob, success, error],
  );

  const filtered = useMemo(() => {
    return jobs.filter((j) => {
      if (format && j.source_format !== format && j.target_format !== format)
        return false;
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
          {PRIMARY_FORMATS.map((f) => (
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
          {/* A chip from the “more” set that is currently active must ALWAYS
              stay visible; the toggle only reveals the remaining ones. */}
          {MORE_FORMATS.filter((f) => format === f).map((f) => (
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
          {showMoreFormats &&
            MORE_FORMATS.filter((f) => format !== f).map((f) => (
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
          <button
            onClick={() => setShowMoreFormats((v) => !v)}
            aria-expanded={showMoreFormats}
            className="rounded-full border border-outline-strong px-3 py-1 text-xs font-semibold text-muted hover:bg-surface-variant"
          >
            {showMoreFormats ? "Less" : "More formats"}
          </button>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Dropdown
            value={range}
            onChange={setRange}
            ariaLabel="Filter by time range"
            align="right"
            options={[
              { value: "all", label: "All time" },
              { value: "24h", label: "Last 24 hours" },
              { value: "7d", label: "Last 7 days" },
              { value: "30d", label: "Last 30 days" },
            ]}
          />
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
        <div className={HISTORY_HEADER_GRID}>
          <span>File</span>
          <span>Format</span>
          <span>Status</span>
          <span className="hidden lg:block">Created</span>
          <span className="text-right">Actions</span>
        </div>

        {filtered.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <p className="font-display text-lg font-semibold">
              Nothing here yet
            </p>
            <p className="mt-1 text-sm text-muted">
              Completed and failed conversions will show up here.
            </p>
            <Link
              to="/app/convert"
              className="mt-4 inline-block text-sm font-medium text-primary hover:underline"
            >
              Convert a file
            </Link>
          </div>
        ) : (
          <ul className="divide-y divide-outline">
            {filtered.map((job) => (
              <HistoryRow
                key={job.job_id}
                job={job}
                compact={compact}
                open={openJobId === job.job_id}
                onToggle={handleToggle}
                onDownload={handleDownload}
                onRetry={handleRetry}
                onDelete={handleDeleteRequest}
              />
            ))}
          </ul>
        )}
      </Card>

      {/* Delete confirmation */}
      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete history record?"
        description={
          deleteTarget
            ? (deleteTarget.fileName ?? deleteTarget.input_file)
            : undefined
        }
      >
        <p className="text-sm text-on-background">
          This will permanently remove this conversion from your history. This
          cannot be undone.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => deleteTarget && handleDelete(deleteTarget)}
          >
            Delete
          </Button>
        </div>
      </Modal>
    </div>
  );
}
