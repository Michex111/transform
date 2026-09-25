import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  CaretDown,
  CaretUp,
  CheckCircle,
  UploadSimple,
  WarningCircle,
  X,
  XCircle,
} from "@phosphor-icons/react";
import { Card, ProgressBar } from "@/components/ui";
import { formatBytes } from "@/lib/format";
import {
  combineProgress,
  formatEta,
  formatProgressPercent,
  isUploadActive,
  summarizeUploads,
} from "@/lib/uploadEta";
import type { UiUpload } from "@/lib/uploadStore";
import { useUploads } from "@/uploads/uploadsContext";

const LIST_ID = "uploads-dock-list";

/**
 * The bottom-right active-uploads popup.
 *
 * Rendered through a portal to `document.body`, which is a documented trap in
 * this repo: a non-`none` `transform` on ANY ancestor makes that ancestor the
 * containing block for `position: fixed` descendants, and the app's page
 * wrapper carries a `translateY(10px)` from its enter animation — so a `fixed`
 * dock left in the tree measures against that wrapper and sits in the wrong
 * place. See the note on `FormatPicker`'s `PopoverPortal`.
 */
export function UploadsDock() {
  const { uploads, cancel, retry, dismiss } = useUploads();
  const [expanded, setExpanded] = useState(false);

  // Announce only *transitions*. A live region on the progress bar itself would
  // read a changing number to the user continuously for the whole upload, which
  // is why the bars carry `role="progressbar"` (queryable on demand) and a
  // separate polite region speaks once when an upload finishes or fails.
  const [announcement, setAnnouncement] = useState("");
  const previousStatuses = useRef<Map<string, string>>(new Map());
  const announceSeeded = useRef(false);

  useEffect(() => {
    // Seed on the first render so rows restored from storage are not announced
    // as if they had just happened. A boolean marker, not "the map is empty":
    // after the user dismisses everything the map is empty again, and the next
    // upload must still be announced.
    if (!announceSeeded.current) {
      announceSeeded.current = true;
      previousStatuses.current = new Map(uploads.map((upload) => [upload.id, upload.status]));
      return;
    }
    let next = "";
    for (const upload of uploads) {
      const was = previousStatuses.current.get(upload.id);
      if (upload.status === "done" && was !== "done") {
        next = `${upload.fileName} uploaded`;
      } else if (upload.status === "failed" && was !== "failed" && !next) {
        next = `${upload.fileName} failed to upload`;
      }
    }
    previousStatuses.current = new Map(uploads.map((upload) => [upload.id, upload.status]));
    if (next) setAnnouncement(next);
  }, [uploads]);

  // The dock unmounts with the last row, so a fresh session starts collapsed.
  if (typeof document === "undefined" || uploads.length === 0) return null;

  const activeUploads = uploads.filter((upload) => isUploadActive(upload.status));
  const finishedUploads = uploads.filter((upload) => !isUploadActive(upload.status));
  // Counts and wording both come from the pure helper, which accounts for
  // every terminal status: `canceled` and `interrupted` are neither `failed`
  // nor `complete`, and the old three-way branch called a cancelled-only dock
  // "1 upload complete". See `summarizeUploads`.
  const summary = summarizeUploads(uploads);
  // Bytes-weighted across whichever set is on screen: averaging per-file
  // percentages would let a small finished file mask a large running one.
  const overall = combineProgress(activeUploads.length > 0 ? activeUploads : finishedUploads);

  return createPortal(
    // Sits above the mobile bottom navigation, which is `lg:hidden` and measures
    // ~60px plus the iOS safe-area inset (the nav wrapper sets
    // `pb-[env(safe-area-inset-bottom)]`), so the offset has to include that
    // inset too or the dock hides behind the nav on a notched phone. From `lg`
    // up there is no nav and the dock returns to the corner.
    <div className="fixed right-4 bottom-[calc(4.5rem_+_env(safe-area-inset-bottom))] z-40 w-80 max-w-[calc(100vw-2rem)] lg:bottom-4">
      <Card className="overflow-hidden shadow-2xl">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-controls={LIST_ID}
          className="flex w-full items-center gap-3 px-3 py-2.5 text-left pointer-coarse:py-3"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-container text-primary">
            {summary.active > 0 ? (
              <UploadSimple size={16} weight="duotone" className="animate-pulse" />
            ) : summary.failed > 0 ? (
              <WarningCircle size={16} weight="duotone" className="text-error" />
            ) : summary.canceled > 0 ? (
              /* A cancel is neither a success nor an error, so it does not get
                 a green tick — the header would be lying twice over. */
              <XCircle size={16} weight="duotone" className="text-muted" />
            ) : (
              <CheckCircle size={16} weight="duotone" className="text-success" />
            )}
          </span>

          <span className="min-w-0 flex-1">
            <span className="flex items-baseline justify-between gap-2">
              <span className="truncate text-sm font-semibold text-on-background">
                {summary.heading}
              </span>
              {activeUploads.length > 0 && (
                <span className="shrink-0 font-mono text-xs text-muted">
                  {overall.percent ?? 0}%
                </span>
              )}
            </span>
            {activeUploads.length > 0 && (
              <ProgressBar
                value={overall.percent}
                ariaLabel="Overall upload progress"
                className="mt-1.5"
              />
            )}
          </span>

          <span className="shrink-0 text-muted" aria-hidden>
            {expanded ? <CaretDown size={16} /> : <CaretUp size={16} />}
          </span>
        </button>

        {/* Kept mounted (hidden) so `aria-controls` always names a real element. */}
        <div
          id={LIST_ID}
          hidden={!expanded}
          className="max-h-80 overflow-y-auto overscroll-contain border-t border-outline"
        >
          <AnimatePresence initial={false}>
            {uploads.map((upload) => (
              <motion.div
                key={upload.id}
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18 }}
              >
                <UploadRow
                  upload={upload}
                  onCancel={() => cancel(upload.id)}
                  onRetry={() => retry(upload.id)}
                  onDismiss={() => dismiss(upload.id)}
                />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

        {/* `polite` so it waits for a pause rather than interrupting the user
            mid-sentence; `sr-only` because the visual state is already there. */}
        <div aria-live="polite" className="sr-only">
          {announcement}
        </div>
      </Card>
    </div>,
    document.body,
  );
}

function UploadRow({
  upload,
  onCancel,
  onRetry,
  onDismiss,
}: {
  upload: UiUpload;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const active = isUploadActive(upload.status);
  const percent = formatProgressPercent(upload.bytesDone, upload.size);

  return (
    <div className="flex items-start gap-2 border-b border-outline px-3 py-3 last:border-b-0">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-medium text-on-background" title={upload.fileName}>
            {upload.fileName}
          </span>
          <span className="shrink-0 text-xs text-muted">{formatBytes(upload.size)}</span>
        </div>

        {active ? (
          <>
            <ProgressBar
              value={percent}
              ariaLabel={`${upload.fileName} upload progress`}
            />
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="text-muted">{activeEstimate(upload)}</span>
              <span className="font-mono text-muted">{percent ?? 0}%</span>
            </div>
          </>
        ) : (
          <p className={`text-xs ${STATUS_TEXT_CLASS[upload.status]}`}>{terminalText(upload)}</p>
        )}
      </div>

      {active ? (
        <button
          type="button"
          onClick={onCancel}
          aria-label={`Cancel upload of ${upload.fileName}`}
          className="-mr-1 shrink-0 rounded-md p-2 text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:p-3"
        >
          <X size={16} />
        </button>
      ) : (
        <div className="flex shrink-0 items-center gap-1">
          {upload.status === "failed" && upload.retryable && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md px-2 py-2 text-xs font-semibold text-primary transition-colors hover:bg-surface-variant pointer-coarse:px-3 pointer-coarse:py-3"
            >
              Retry
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            aria-label={`Dismiss ${upload.fileName}`}
            className="-mr-1 rounded-md p-2 text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:p-3"
          >
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

/** The estimate line for a row that is still moving. */
function activeEstimate(upload: UiUpload): string {
  if (upload.status === "queued") return "Waiting to start";
  if (upload.status === "verifying") return "Finishing up…";
  return formatEta(upload.etaSeconds ?? null);
}

function terminalText(upload: UiUpload): string {
  switch (upload.status) {
    case "done":
      return "Uploaded";
    case "canceled":
      return "Canceled";
    case "failed":
      return upload.error ?? "Upload failed";
    case "interrupted":
      return upload.error ?? "Interrupted — select the file again";
    default:
      return "";
  }
}

const STATUS_TEXT_CLASS: Record<UiUpload["status"], string> = {
  queued: "text-muted",
  uploading: "text-muted",
  verifying: "text-muted",
  done: "text-success",
  failed: "text-error",
  canceled: "text-muted",
  interrupted: "text-warning",
};
