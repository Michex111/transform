import { useEffect, useRef, useState } from "react";
import { CircleNotch, CloudArrowUp, DownloadSimple } from "@phosphor-icons/react";
import type { AssistantArtifact } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { FormatThumb } from "@/components/FormatThumb";
import { Button, FormatMorph, ProgressBar, StatusBadge } from "@/components/ui";
import { FINISHED_STATUSES, jobProgress, type UiJob } from "@/jobs/jobStore";
import { applyJobProgress } from "@/lib/assistantChat";
import { conversionHeadline, conversionIdentity } from "@/lib/conversionCard";
import { formatBytes, formatMeta } from "@/lib/format";
import { useSaveToDrive } from "@/lib/useSaveToDrive";

/**
 * How many times a dropped progress stream is reattached before giving up, and
 * how long to wait between attempts.
 *
 * A dropped stream says nothing about the job, so the card keeps its last known
 * state and tries to reconnect — otherwise the bar freezes at whatever value it
 * had (or stays indeterminate) for the rest of the chat. The budget is small
 * because each attempt re-fetches the job; a persistently unreachable API is
 * not something a card should hammer.
 */
const MAX_STREAM_RETRIES = 5;
const STREAM_RETRY_DELAY_MS = 3000;

/**
 * The live card for a conversion the assistant started.
 *
 * A `job` artifact used to render as a static chip linking to the queue, which
 * told the user nothing about the work they had just asked for. This replaces it
 * inside the answer: it fetches the job, follows its SSE stream while it runs,
 * and ends with the two things a finished conversion is *for* — Download and
 * Save to Drive. The same calls the Convert page makes, so the two surfaces
 * cannot drift.
 *
 * It renders as a plain block, never a `Link`: the card owns real controls, and
 * a row that both navigates and holds buttons is a trap on touch.
 */
export function ConversionCard({ artifact }: { artifact: AssistantArtifact }) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const { savingId, saveToDefaultFolder } = useSaveToDrive();

  const jobId = artifact.id;
  const [job, setJob] = useState<UiJob | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // One flag, not a set: the card has a single row of controls, and both
  // controls disable while either action is in flight so they cannot fight over
  // the row's busy state.
  const [downloading, setDownloading] = useState(false);

  // Fetch once, then follow the job's progress stream until it reaches a
  // terminal state. The cleanup always unsubscribes, so scrolling a transcript
  // away (or unmounting the whole chat) never leaves an SSE connection open.
  //
  // `retryTick` re-runs this effect after a dropped stream so the card
  // re-fetches the job (now carrying the persisted progress) and reattaches.
  const [retryTick, setRetryTick] = useState(0);
  const retries = useRef(0);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;
    let retryTimer: number | null = null;

    const retry = () => {
      // A dropped stream (proxy hiccup, redeploy, offline tab) says nothing
      // about the job itself, so the last known status is kept — only the
      // server's own terminal event may report an outcome. But it must not
      // stop the bar updating forever, so reattach a bounded number of times.
      if (cancelled || retries.current >= MAX_STREAM_RETRIES) return;
      retries.current += 1;
      retryTimer = window.setTimeout(() => {
        if (!cancelled) setRetryTick((tick) => tick + 1);
      }, STREAM_RETRY_DELAY_MS);
    };

    client
      .getJob(jobId)
      .then((fetched) => {
        if (cancelled) return;
        setJob(fetched);
        // A historical conversation must not open a connection per job: only a
        // job that is still running has anything live to report, and the server
        // has no events left to send for a finished one.
        if (FINISHED_STATUSES.has(fetched.status)) return;
        unsubscribe = client.subscribeToJob(jobId, {
          onProgress: (event) =>
            setJob((previous) => (previous ? applyJobProgress(previous, event) : previous)),
          onConnected: () => {
            // A successful (re)connection clears the budget, so the limit is on
            // *consecutive* failures rather than a card's lifetime.
            retries.current = 0;
          },
          onError: retry,
          onDone: () => {},
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(
          err instanceof Error ? err.message : "Could not load this conversion.",
        );
      });

    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      unsubscribe?.();
    };
  }, [client, jobId, retryTick]);

  // The artifact carries the formats when the assistant started the job; the
  // fetched job is the fallback for a row the server persisted without them.
  const sourceFormat =
    metaString(artifact.meta, "source_format") ?? job?.source_format ?? "";
  const targetFormat =
    metaString(artifact.meta, "target_format") ?? job?.target_format ?? "";
  const { name, sourceName, outputName, actionName } = conversionIdentity(artifact, job);

  const status = job?.status ?? null;
  const completed = status === "COMPLETED";
  // Terminal either way: a finished job's format morph stops animating and its
  // progress bar is retired in favour of the header's own status.
  const finished = FINISHED_STATUSES.has(status ?? "");
  const saving = savingId === jobId;
  const busy = saving || downloading;
  const showFormats = sourceFormat !== "" && targetFormat !== "";

  async function download() {
    if (busy || !jobId) return;
    setDownloading(true);
    try {
      // Never build the URL here: the job's own route is what decides between a
      // pre-signed URL and the authenticated streaming path that decrypts an
      // at-rest-encrypted output, and it owns the filename the worker produced.
      await client.downloadConvertedFile(jobId);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not download file");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div
      // `w-full min-w-0` keeps it inside the assistant column; the card is a
      // full-width row rather than a chip, so it must not report an intrinsic
      // width of its own.
      className="w-full min-w-0 overflow-hidden rounded-xl border border-outline bg-surface-variant/40"
      role="group"
      aria-label={`Conversion for ${name}`}
    >
      {/* Header strip: what happened, in words, plus the job's own status.
          The verb is derived from the real job status, never from a client
          timer, so the card cannot claim a completion the backend has not
          confirmed. */}
      <div className="flex min-w-0 items-center gap-2 border-b border-outline/70 bg-surface/40 px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wide text-muted">
          {conversionHeadline(status)}
        </span>
        {status && (
          <span className="shrink-0">
            <StatusBadge status={status} />
          </span>
        )}
      </div>

      <div className="min-w-0 space-y-3 p-3">
        {/* The source. Named from the job's own `input_file`, so the file the
            user handed over is always presented separately from the one the
            worker produced. */}
        <ConversionFileRow
          label="Original"
          format={sourceFormat || sourceName}
          filename={sourceName}
          sizeBytes={job?.input_size_bytes}
        />

        {showFormats && (
          // Ornamental motion only while there is something to wait for; a
          // finished job's morph is static, like every other terminal row.
          <div className="flex min-w-0 items-center justify-center py-0.5">
            <FormatMorph from={sourceFormat} to={targetFormat} size="sm" animated={!finished} />
          </div>
        )}

        {/* The result. A filename appears only once the worker has written one:
            until then the row names the format being produced rather than
            guessing a name from the source file. */}
        <ConversionFileRow
          label="Converted file"
          format={targetFormat || name}
          filename={outputName}
          pendingLabel={targetFormat ? `${formatMeta(targetFormat).label} pending` : "Pending"}
          sizeBytes={job?.output_size_bytes}
        />

        {/* A failed job has no honest bar to draw — the last percentage says
            nothing about an outcome that will never arrive — so the error is the
            whole status line for it. A finished job retires the bar entirely:
            the header strip and its Ready/Failed badge already say what a
            100%-filled bar would only repeat. A job with no real percentage
            renders the indeterminate bar rather than an invented number. */}
        {!finished && !loadError && (
          <ProgressBar
            value={job ? jobProgress(job) : null}
            from={sourceFormat ? formatMeta(sourceFormat).color : "var(--color-primary)"}
            to={targetFormat ? formatMeta(targetFormat).color : undefined}
            ariaLabel={`${name} conversion progress`}
          />
        )}

        {loadError && <p className="min-w-0 break-words text-xs text-error">{loadError}</p>}

        {job?.status === "FAILED" && (job.error_message || job.errorMessage) && (
          // The server's own reason, verbatim. Nothing is invented when it sent
          // none.
          <p className="min-w-0 break-words text-xs leading-relaxed text-error">
            {job.error_message || job.errorMessage}
          </p>
        )}

        {completed && job && (
          // Two compact actions, both acting on the OUTPUT file. On a touch
          // device the words are dropped and the icons stand alone, side by
          // side: they are unambiguous, and this card sits in a chat column that
          // is already narrow on a phone. The rule is keyed on POINTER CAPABILITY
          // (`pointer-coarse`), not width, so a landscape phone — wide, but with
          // no pointer — still gets the compact form, which is the same rule the
          // rest of this app's touch affordances use.
          //
          // `min-h-9 min-w-9` (36px), not the app-wide 44px: two square icon
          // buttons set the height of the row they sit in, and at 44px they read
          // as oversized next to the 12px metadata line above them. 36px still
          // clears the touch-target floor this app holds itself to.
          //
          // Both buttons therefore need an explicit `aria-label`: hiding the text
          // with `display: none` removes it from the accessible name, so without
          // one a screen reader would announce nothing but "button".
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={() => void download()}
              disabled={busy}
              aria-label={downloading ? `Downloading ${actionName}` : `Download ${actionName}`}
              className="pointer-coarse:min-h-9 pointer-coarse:min-w-9 pointer-coarse:px-2"
            >
              {downloading ? (
                <CircleNotch size={15} className="animate-spin" aria-hidden />
              ) : (
                <DownloadSimple size={15} aria-hidden />
              )}
              <span className="pointer-coarse:hidden">
                {downloading ? "Downloading…" : "Download"}
              </span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void saveToDefaultFolder(job)}
              disabled={busy}
              aria-label={
                saving ? `Saving ${actionName} to your drive` : `Save ${actionName} to your drive`
              }
              className="pointer-coarse:min-h-9 pointer-coarse:min-w-9 pointer-coarse:px-2"
            >
              {/* A spinner, not just the word: once the label is hidden on touch,
                  the icon is the only thing that can report that a save started. */}
              {saving ? (
                <CircleNotch size={15} className="animate-spin" aria-hidden />
              ) : (
                <CloudArrowUp size={15} aria-hidden />
              )}
              <span className="pointer-coarse:hidden">
                {saving ? "Saving…" : "Save to Drive"}
              </span>
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * One side of a conversion: which file it is, its format tile, and — only when
 * the worker actually measured it — its size.
 *
 * The name is never derived here: the source row is given the server's
 * `input_file` and the result row the server's `output_file`, so the two can
 * never be mistaken for one another. A result that does not exist yet renders
 * its `pendingLabel` instead of a filename.
 *
 * Exported for its tests: the size-omission and pending-name rules are the parts
 * most likely to regress silently.
 */
export function ConversionFileRow({
  label,
  format,
  filename,
  pendingLabel,
  sizeBytes,
}: {
  label: string;
  format: string;
  filename: string | null;
  pendingLabel?: string;
  sizeBytes?: number;
}) {
  // `0` means "not measured" (see `ConversionJobResponse`) and is omitted rather
  // than rendered as a zero-byte file.
  const size = typeof sizeBytes === "number" && sizeBytes > 0 ? formatBytes(sizeBytes) : null;

  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <FormatThumb format={format} size="sm" label="" className="shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</p>
        <p
          className={`min-w-0 truncate text-sm font-medium ${
            filename ? "text-on-background" : "text-muted"
          }`}
          // A truncated filename must still be recoverable without hover being
          // the only route: the full name is in the tooltip *and* in the DOM.
          title={filename ?? undefined}
        >
          {filename ?? pendingLabel ?? "Pending"}
        </p>
      </div>
      {size && <p className="shrink-0 text-xs tabular-nums text-muted">{size}</p>}
    </div>
  );
}

/** A trimmed string from an artifact's untyped `meta`, or `null`. */
function metaString(meta: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = meta?.[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}
