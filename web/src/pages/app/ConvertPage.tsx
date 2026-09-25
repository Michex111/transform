import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowRight,
  Broom,
  CircleNotch,
  CloudArrowUp,
  DownloadSimple,
  FolderSimplePlus,
  UploadSimple,
  Swap,
  ArrowCounterClockwise,
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { useJobs, type UiJob } from "@/jobs/JobsContext";
import { activeJobs, jobProgress, recentFinishedJobs, showsProgressBar } from "@/jobs/jobStore";
import { cacheFileForJob } from "@/lib/fileCache";
import { friendlyErrorMessage } from "@/lib/errorMessages";
import { fileNameExtension } from "@/lib/format";
import { canSaveToDrive } from "@/lib/saveToDrive";
import { useRetryConversion } from "@/lib/useRetryConversion";
import { useSaveToDrive } from "@/lib/useSaveToDrive";
import { useConversionMap } from "@/lib/useConversionMap";
import { useNarrowViewport } from "@/lib/useMediaQuery";
import { FolderPickerModal } from "@/components/FolderPickerModal";
import { ErrorButton } from "@/components/ErrorButton";
import { FormatPicker } from "@/components/FormatPicker";
import { RowMenu } from "@/components/RowMenu";
import { Button, Card, FormatMorph, FormatChip, ProgressBar, StatusBadge } from "@/components/ui";

/**
 * One inline queue row: file name, source → target chips, status, optional
 * progress bar, and (for finished rows) the controls that act on the result.
 *
 * LAYOUT: always a single line — `[name] [status] [progress?] [actions?]`.
 *
 * It used to wrap the controls onto a second line below `xl`, because the
 * action cell (397px) was wide enough to squash the `1fr` filename column down
 * to 9.5px at a 1024px viewport. Two changes removed the need: the Download
 * control became icon-only, and a COMPLETED row no longer renders the progress
 * bar at all — the bar is at 100% and the "Ready" badge already says so. That
 * leaves a finished row needing only name + 68px status + 219px actions, which
 * fits from `md` up (measured: a 320px filename gets 334px of room at a 768px
 * viewport, growing to 521px by 1280px, where the card reaches its `max-w-4xl`
 * ceiling).
 *
 * The progress bar is the one column whose presence varies by both status and
 * width, so it is rendered `hidden` below its own breakpoint rather than the
 * grid carrying two templates per status: a `display:none` grid item is skipped
 * entirely, so the visible items and the declared tracks still line up. A FAILED
 * row keeps the bar on desktop only — on a phone the row has no room for it and
 * a bar that will never move again is noise next to the "Failed" badge.
 */
function InlineQueueRow({
  job,
  action,
  narrow,
}: {
  job: UiJob;
  action?: ReactNode;
  /** True below Tailwind's `md`, where a finished row's actions move into a menu. */
  narrow: boolean;
}) {
  const name = job.fileName ?? job.input_file;
  const failed = job.status === "FAILED";
  const showProgress = showsProgressBar(job, { narrow });

  // A failed row is the only one that shows a bar *and* actions, so it needs the
  // fourth track; a completed row is three tracks at every width. Both switch at
  // `md` — the same breakpoint as `useNarrowViewport`, so `showsProgressBar`
  // and the CSS below can never disagree about whether the bar is rendered.
  const grid = failed
    ? "grid-cols-[1fr_auto_auto] md:grid-cols-[1fr_auto_140px_auto]"
    : action
      ? "grid-cols-[1fr_auto_auto]"
      : "grid-cols-[1fr_auto] sm:grid-cols-[1fr_auto_140px]";

  return (
    <li className={`grid items-center gap-4 px-5 py-3 ${grid}`}>
      <div className="min-w-0">
        {/* `title` so a truncated name is always recoverable: the name truncates
            on a phone and whenever it outgrows its column's budget. */}
        <p className="truncate text-sm text-on-background" title={name}>
          {name}
        </p>
        <div className="mt-1 flex items-center gap-2">
          <FormatChip format={job.source_format} size="xs" />
          <ArrowRight size={12} className="text-muted" />
          <FormatChip format={job.target_format} size="xs" />
        </div>
      </div>
      <StatusBadge status={job.status} />
      {showProgress && (
        <div className={failed ? "hidden md:block" : "hidden sm:block"}>
          <ProgressBar
            value={jobProgress(job)}
            from="var(--color-primary)"
          />
        </div>
      )}
      {action && <div className="justify-self-end">{action}</div>}
    </li>
  );
}

export function ConvertPage() {
  const { api: client } = useAuth();
  const { addJob, jobs, refresh, queueClearedAt, clearQueue } = useJobs();
  const { success, error } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const prefill = (location.state as { source?: string; target?: string } | null) ?? {};

  // Filing a finished conversion into the drive, shared with the History panel
  // (see `lib/useSaveToDrive.ts`): the output is read through the job's own
  // download route — an at-rest-encrypted output is only readable there, where
  // the server decrypts it — and then handed to the background upload manager,
  // which owns the transfer and the dock.
  const { savingId, saveToDefaultFolder, saveToFolder } = useSaveToDrive();
  // The completed row whose destination dialog is open, if any.
  const [pickJob, setPickJob] = useState<UiJob | null>(null);

  // Load server history on mount so the inline queue isn't empty on a fresh
  // session.
  useEffect(() => {
    refresh();
  }, [refresh]);

  const [from, setFrom] = useState(prefill.source ?? "pdf");
  const [to, setTo] = useState(prefill.target ?? "docx");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Source → valid target formats, served from the shared conversion-map store:
  // it is already populated from cache on the first render, so the pickers below
  // never offer formats the backend cannot convert.
  const { sources: allowedSources, targetsFor, loading: mapLoading } = useConversionMap();

  // The target formats currently allowed for the chosen source.
  const allowedTargets = useMemo(() => targetsFor(from), [targetsFor, from]);

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

  // One shared "still in flight" predicate (it counts AWAITING_UPLOAD as
  // active), so the inline queue matches the Queue page exactly.
  const inlineQueue = useMemo(() => activeJobs(jobs).slice(0, 4), [jobs]);

  // Finished conversions from the last 30 minutes (see
  // `RECENT_FINISHED_WINDOW_MS`), newest first — completions *and* failures,
  // because a failed row can be retried from here. `clearedBefore` is the marker
  // the Clear control wrote, so a cleared list stays cleared across reloads
  // while a job that finishes afterwards still appears.
  const recentFinished = useMemo(
    () => recentFinishedJobs(jobs, { now: Date.now(), clearedBefore: queueClearedAt }),
    [jobs, queueClearedAt],
  );

  // Below `md` a finished row has no space for its controls inline, so they move
  // into the row's ⋮ menu. Resolved once for the page rather than per row.
  const narrow = useNarrowViewport();

  // Retrying a failed conversion, shared with the History row: re-enqueue when
  // the input is still in storage, re-upload from this tab's cache when it is
  // not, and pre-fill Convert when there is nothing left to retry with.
  const { retryingId, retry } = useRetryConversion();

  // The finished row whose output is being read for a plain download. One id,
  // not a set: the row disables both of its controls while it is set (see
  // `busy` below), so a second click cannot start a competing fetch.
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  /**
   * Download a finished conversion's output straight from the queue.
   *
   * Goes through `client.downloadConvertedFile`, the same call the Dashboard and
   * the History page use, rather than building a URL here: it re-reads the job
   * (the output's location is what decides between a pre-signed URL and the
   * authenticated streaming route that decrypts an at-rest-encrypted output),
   * applies the download-URL scheme allowlist, recovers from an expired token
   * once, and derives the filename from the object the worker actually produced
   * — so a multi-page `pdf -> png` job still saves as the `.zip` it really is.
   */
  async function downloadJob(job: UiJob) {
    if (downloadingId !== null) return;
    setDownloadingId(job.job_id);
    try {
      await client.downloadConvertedFile(job.job_id);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not download file");
    } finally {
      setDownloadingId(null);
    }
  }

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
    const ext = fileNameExtension(dropped.name);
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

    // Enforce the advertised upload limit client-side so users aren't surprised
    // by a 413 after a slow upload.
    const MAX_BYTES = 100 * 1024 * 1024; // 100 MB
    if (file.size > MAX_BYTES) {
      error(`File is too large. The maximum upload size is 100 MB.`);
      return;
    }

    setBusy(true);
    try {
      // Run the full conversion flow: encrypt (for files < 1 GB), create job,
      // upload file, verify/enqueue. The client encrypts the file to a FENCR
      // blob before upload and passes the data key; if the deployment has no
      // master key it falls back to plaintext automatically.
      const job = await client.convertWithFile(from, to, file);

      // Cache the file against the job id so a later retry can re-upload it
      // without the user re-selecting the file. We always cache the ORIGINAL
      // plaintext File — encryption is a per-attempt concern handled inside
      // convertWithFile, so a retry re-encrypts fresh.
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
        <div className="flex items-center justify-center gap-4 sm:gap-6">
          <div className="flex flex-col items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-muted">From</span>
            <FormatPicker
              value={from}
              onChange={setFrom}
              ariaLabel="Choose source format"
              align="left"
              allowed={allowedSources}
              pending={mapLoading}
            />
          </div>

          <div className="flex flex-col items-center gap-1">
            <motion.button
              onClick={() => {
                // Swap, then ensure the new target is valid for the new source.
                const newFrom = to;
                const newTo = from;
                const targets = targetsFor(newFrom);
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
            <FormatPicker
              value={to}
              onChange={setTo}
              ariaLabel="Choose target format"
              align="right"
              allowed={allowedTargets}
              pending={mapLoading}
            />
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
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Queue</h2>
          <div className="flex items-center gap-3">
            {/* Client-side display only: this hides the finished rows below, it
                never deletes a job — History keeps reporting them. Rendered only
                when there is something to clear. */}
            {recentFinished.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  clearQueue();
                  success("Cleared the recent list");
                }}
                className="pointer-coarse:min-h-11"
              >
                <Broom size={16} />
                Clear
              </Button>
            )}
            <Link to="/app/queue" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
              View full queue <ArrowRight size={16} />
            </Link>
          </div>
        </div>
        {inlineQueue.length === 0 && recentFinished.length === 0 ? (
          // The empty state speaks for both sections, so it is written once.
          <p className="px-5 py-10 text-center text-sm text-muted">
            Nothing in the queue right now.
          </p>
        ) : (
          <div className="divide-y divide-outline">
            {inlineQueue.length > 0 && (
              <section aria-labelledby="queue-active-heading">
                <h3
                  id="queue-active-heading"
                  className="px-5 pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-muted"
                >
                  Active
                </h3>
                <ul className="divide-y divide-outline">
                  {inlineQueue.map((job) => (
                    <InlineQueueRow key={job.job_id} job={job} narrow={narrow} />
                  ))}
                </ul>
              </section>
            )}

            {/* Finished in the last 30 minutes (RECENT_FINISHED_WINDOW_MS),
                timed from when this session saw the job finish and falling back
                to its start time for rows restored from the server (which has
                no finish timestamp). Named "finished" rather than "completed"
                because a failed conversion belongs here too — that is where its
                error and Retry are. Clear hides rows at or before the marker it
                stores locally; the jobs stay in History. */}
            {recentFinished.length > 0 && (
              <section aria-labelledby="queue-recent-heading">
                <h3
                  id="queue-recent-heading"
                  className="px-5 pt-4 pb-1 text-xs font-medium uppercase tracking-wide text-muted"
                >
                  Recently finished
                </h3>
                <ul className="divide-y divide-outline">
                  {recentFinished.map((job) => {
                    // One transfer at a time across the whole list, not just per
                    // row: `savingId` and `downloadingId` are single values and
                    // both paths read the output through the same route, so a
                    // second job's fetch would take over the first's busy state
                    // and leave its row looking idle while it still worked.
                    const busy = savingId !== null || downloadingId !== null;
                    const saving = savingId === job.job_id;
                    const downloading = downloadingId === job.job_id;
                    const retrying = retryingId === job.job_id;
                    const name = job.fileName ?? job.input_file;
                    const failed = job.status === "FAILED";

                    // ---- Completed: get the file, or file it away ----
                    const downloadButton = (
                      /* The finish-line action, so it carries the row's only
                          emphasis: getting the file is what a completed row is
                          for. Icon-only because the download glyph needs no
                          label, which is what keeps the action set narrow enough
                          to sit beside a long filename. */
                      <button
                        type="button"
                        onClick={() => void downloadJob(job)}
                        disabled={busy}
                        aria-label={downloading ? `Downloading ${name}` : `Download ${name}`}
                        title={downloading ? "Downloading…" : "Download"}
                        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-on-primary transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                      >
                        {downloading ? (
                          <CircleNotch size={16} className="animate-spin" aria-hidden />
                        ) : (
                          <DownloadSimple size={16} aria-hidden />
                        )}
                      </button>
                    );

                    const saveToDriveButton = (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => void saveToDefaultFolder(job)}
                        disabled={busy}
                        className="pointer-coarse:min-h-11"
                      >
                        <CloudArrowUp size={16} aria-hidden />
                        {saving ? "Saving…" : "Save to Drive"}
                      </Button>
                    );

                    const chooseFolderButton = (
                      <button
                        type="button"
                        onClick={() => setPickJob(job)}
                        disabled={busy}
                        aria-label={`Choose where to save ${name}`}
                        title="Choose where to save"
                        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-outline-strong text-muted transition-colors hover:bg-surface-variant hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                      >
                        <FolderSimplePlus size={16} />
                      </button>
                    );

                    // ---- Failed: read the reason, or run it again ----
                    const retryButton = (
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => void retry(job)}
                        disabled={retrying}
                        className="pointer-coarse:min-h-11"
                      >
                        <ArrowCounterClockwise size={16} aria-hidden />
                        {retrying ? "Retrying…" : "Retry"}
                      </Button>
                    );

                    const actions = failed ? (
                      // On a desktop row the reason is a hover/click popover and
                      // Retry sits beside it. On a phone neither fits, so the ⋮
                      // menu carries both — with the message as *text* in the
                      // menu, because a popover inside a menu is a nested
                      // interaction, and on touch the message is the thing the
                      // user opened the menu to read.
                      narrow ? (
                        <RowMenu
                          label={`More options for ${name}`}
                          disabled={retrying}
                          items={[
                            {
                              key: "error",
                              label: "Retry",
                              icon: <ArrowCounterClockwise size={15} aria-hidden />,
                              detail: friendlyErrorMessage(job.errorMessage),
                              onSelect: () => void retry(job),
                            },
                          ]}
                        />
                      ) : (
                        <>
                          <ErrorButton message={job.errorMessage} />
                          {retryButton}
                        </>
                      )
                    ) : canSaveToDrive(job) ? (
                      narrow ? (
                        // One tap for the file, the rest behind ⋮: the row keeps
                        // the action most completions want and still fits.
                        <>
                          {downloadButton}
                          <RowMenu
                            label={`More options for ${name}`}
                            disabled={busy}
                            items={[
                              {
                                key: "save",
                                label: saving ? "Saving…" : "Save to Drive",
                                icon: <CloudArrowUp size={15} aria-hidden />,
                                onSelect: () => void saveToDefaultFolder(job),
                              },
                              {
                                key: "choose",
                                label: "Choose where to save",
                                icon: <FolderSimplePlus size={15} aria-hidden />,
                                onSelect: () => setPickJob(job),
                              },
                            ]}
                          />
                        </>
                      ) : (
                        <>
                          {downloadButton}
                          {saveToDriveButton}
                          {chooseFolderButton}
                        </>
                      )
                    ) : null;

                    return (
                      <InlineQueueRow
                        key={job.job_id}
                        job={job}
                        narrow={narrow}
                        action={
                          // `justify-end` keeps the controls against the row's
                          // trailing edge; `shrink-0` stops the action cell
                          // itself being squeezed now that it shares the line.
                          <div className="flex shrink-0 items-center justify-end gap-2">
                            {actions}
                          </div>
                        }
                      />
                    );
                  })}
                </ul>
              </section>
            )}
          </div>
        )}
      </Card>

      {/* One-off destination for a completed row. The dialog can also make the
          choice the default, which is persisted only after the save is already
          queued. */}
      <FolderPickerModal
        open={pickJob !== null}
        onClose={() => setPickJob(null)}
        allowSetDefault
        title="Save to a folder"
        hint={
          pickJob
            ? `Choose a folder for ${pickJob.fileName ?? pickJob.input_file}.`
            : undefined
        }
        onConfirm={async (folderId, useAsDefault) => {
          if (!pickJob) return;
          await saveToFolder(pickJob, folderId, { useAsDefault });
        }}
      />
    </div>
  );
}
