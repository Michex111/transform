/**
 * Time-estimate and progress helpers behind the background uploads dock.
 *
 * Pure and DOM-free so they can be unit-tested in this repo's Node test
 * environment (there is no jsdom here) — the same reasoning as `jobStore.ts`.
 *
 * ## Why a blend of two rates, and not a smoothed one plus a ratchet
 *
 * The obvious estimate — `remainingBytes / instantaneousRate` — is computed
 * from two consecutive progress events and therefore reports whatever the last
 * few hundred kilobytes happened to do. On a real connection that swings between
 * "40 seconds left" and "3 minutes left" from one sample to the next, which
 * reads as a broken UI and is the single most obvious sign of an amateur
 * implementation.
 *
 * An earlier version of this module smoothed the rate with an EWMA and then
 * clamped the *result* so it could never rise. That fixed the oscillation and
 * introduced something worse: an unrecoverable running minimum. The clamp is
 * only ever released by a rate that has fallen 30%, so an early over-estimate
 * of throughput is permanent, and because the reading is floored at one second
 * it freezes at "1 second left" while the bar keeps moving. It reproduced
 * reliably on a real 200 MiB multipart upload: `1 second left` from 10% to
 * 100%, 13.6 seconds of transfer. A number that never moves is a worse lie than
 * a number that wobbles, so the clamp is gone and nothing has replaced it:
 *
 *  1. The EWMA stays, for responsiveness, but each sample's weight is now
 *     scaled by its own time step (`1 - (1 - alpha) ^ (dt / reference)`). Three
 *     parallel parts report their first buffered chunks within a few
 *     milliseconds of each other, so a fixed alpha gave a 5 ms, 18 MiB sample
 *     the same authority as an honest half-second one; a 5 ms sample now moves
 *     the average by ~0.5% instead of 30%.
 *  2. That is blended with the *overall* rate since the transfer started,
 *     `bytesDone / elapsed`. An opening burst is a fixed number of bytes, so
 *     its share of the overall average decays as `1/t` — the term is
 *     self-correcting by construction. Early on the blend leans on it (the
 *     EWMA is still full of the burst); after a few seconds the EWMA has
 *     forgotten the burst and carries most of the weight.
 *
 * Both terms decay toward the truth, so the estimate does too, and it can move
 * in either direction the way a real transfer does. No clamp of any kind is
 * applied to the *value*, because every clamp that bounds the reading against
 * its own history is the running-minimum defect wearing a different hat: it can
 * always be stranded by an early error. Smoothness is bought by the blend
 * (one outlier moves the EWMA by `alpha` and the overall term by `1/n`), never
 * by forbidding movement.
 */

// ---- Status vocabulary ----
//
// Declared here rather than in the store so both modules can share it without
// the store having to import the ETA module back.
export type UploadStatus =
  | "queued"
  | "uploading"
  | "verifying"
  | "done"
  | "failed"
  | "canceled"
  | "interrupted";

/** Statuses that mean bytes are still being moved (or are about to be). */
export const ACTIVE_UPLOAD_STATUSES: ReadonlySet<UploadStatus> = new Set([
  "queued",
  "uploading",
  "verifying",
]);

/** Statuses an upload never leaves. `interrupted` is the hydrate-time failure. */
export const TERMINAL_UPLOAD_STATUSES: ReadonlySet<UploadStatus> = new Set([
  "done",
  "failed",
  "canceled",
  "interrupted",
]);

/** True while an upload still counts as "in flight" (so belongs in the dock). */
export function isUploadActive(status: UploadStatus): boolean {
  return ACTIVE_UPLOAD_STATUSES.has(status);
}

/**
 * True only while an upload is actually moving or finalising bytes.
 *
 * This is the *scheduling* predicate, and it is deliberately narrower than
 * {@link isUploadActive}: `queued` means "waiting for a slot", so counting a
 * waiting row as running makes the queue pump compute its own capacity as zero
 * and start nothing at all. Measured before the split: a 2-file drop (and a
 * 5-file drop) deadlocked at "Waiting to start" with **zero** network requests,
 * because two `queued` rows consumed both of `MAX_CONCURRENT_FILE_UPLOADS`
 * slots before a single transfer had begun.
 *
 * Display keeps using `isUploadActive` — a queued row IS in flight for the
 * dock's "Uploading N files" heading, and must stay visible there.
 */
export function isUploadRunning(status: UploadStatus): boolean {
  return status === "uploading" || status === "verifying";
}

/** True once an upload has reached an outcome and will not progress further. */
export function isUploadTerminal(status: UploadStatus): boolean {
  return TERMINAL_UPLOAD_STATUSES.has(status);
}

// ---- Dock summary ----

/** What the dock's header says, and the counts the icon is chosen from. */
export interface UploadsDockSummary {
  /** Uploads still moving (or about to). */
  active: number;
  done: number;
  /** `failed` *and* `interrupted` — both are failures, not outcomes. */
  failed: number;
  canceled: number;
  /** A true one-line statement about the list, for any mix of statuses. */
  heading: string;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * Summarise an upload list for the dock header.
 *
 * Exists because the header used to be a three-way choice — uploading, failed,
 * complete — and `canceled`/`interrupted` are neither of the last two. A dock
 * holding one `Canceled` row therefore read "1 upload complete", which is a
 * false statement about the user's data. Every terminal status is counted here,
 * and `done` is the only one allowed to produce the word "complete".
 *
 * `interrupted` (the hydrate-time failure: the bytes were in memory and the tab
 * closed) is counted as failed, because that is what it is — the user has to
 * select the file again — and it keeps a batch from reading as if it succeeded.
 */
export function summarizeUploads(
  uploads: readonly { status: UploadStatus }[],
): UploadsDockSummary {
  const active = uploads.filter((upload) => isUploadActive(upload.status)).length;
  const done = uploads.filter((upload) => upload.status === "done").length;
  const canceled = uploads.filter((upload) => upload.status === "canceled").length;
  const failed = uploads.filter(
    (upload) => upload.status === "failed" || upload.status === "interrupted",
  ).length;

  let heading: string;
  if (active > 0) {
    heading = `Uploading ${plural(active, "file")}`;
  } else {
    // Joined rather than ranked, so a batch that lost one file to a failure and
    // another to a cancel reports both instead of only the more dramatic one.
    const parts: string[] = [];
    if (done > 0) parts.push(`${plural(done, "upload")} complete`);
    if (failed > 0) parts.push(`${plural(failed, "upload")} failed`);
    if (canceled > 0) parts.push(`${plural(canceled, "upload")} canceled`);
    heading = parts.length > 0 ? parts.join(" · ") : "Uploads";
  }

  return { active, done, failed, canceled, heading };
}

// ---- ETA ----

/** Weight given to the newest throughput sample, for a reference-length step. */
export const DEFAULT_ETA_ALPHA = 0.3;
/**
 * The progress cadence `DEFAULT_ETA_ALPHA` is calibrated against.
 *
 * An `XMLHttpRequest` fires `upload.onprogress` roughly every 400 ms, and the
 * engine forwards one aggregate report per part event, so this is the step a
 * "normal" sample has. A sample's actual weight is scaled by its own time step
 * relative to this one (see `sample()`), which is what keeps a 5 ms step —
 * three parts' first buffered chunks landing together — from being treated as
 * 400 ms of evidence.
 */
export const ETA_REFERENCE_SAMPLE_SECONDS = 0.4;
/** Samples needed before an estimate is trustworthy. */
export const MIN_ETA_SAMPLES = 2;
/**
 * Wall-clock span the samples must cover before an estimate is shown.
 *
 * Two samples 50 ms apart measure a burst, not a connection, so a real span of
 * transfer is required before any number is shown. The threshold is set by
 * measurement, not taste: a live 200 MB multipart upload was sampled every
 * 400 ms and the estimate was up to **5x pessimistic** in the first ~2 s
 * ("about 1 min left" with 12 s actually remaining), because a transfer is
 * still ramping then — the connection is being set up, the object store is
 * accepting its first parts, and the session/part API round trips have already
 * elapsed without moving a byte.
 *
 * Three seconds covers that ramp. Holding "Calculating…" for it is the honest
 * answer, and it is what this module already promises: a wrong number is worse
 * than an honest wait. Beyond the ramp the same measurement showed the estimate
 * at **1.31x / 1.12x / 0.91x** of the true remainder at 25% / 50% / 75%, so
 * nothing later is being hidden.
 *
 * Do not lower this back to "show something sooner": an estimate that opens
 * with a five-fold overstatement reads as a broken feature, which is exactly
 * the bug this guard exists to prevent.
 */
export const MIN_ETA_SPAN_MS = 3000;
/** The floor: never claim less than a second remains, and never claim zero. */
export const MIN_REMAINING_SECONDS = 1;
/** The ceiling, so a near-zero rate cannot print an absurd figure. */
export const MAX_REMAINING_SECONDS = 24 * 60 * 60;
/**
 * How long the recent (EWMA) rate takes to earn its full share of the blend.
 *
 * Before this has elapsed the overall average gets the larger share, because
 * the EWMA is still carrying the opening burst; after it, the burst has aged
 * out of the EWMA (a few seconds of samples at `alpha` 0.3 leaves ~1% of it)
 * and the recent rate is the better description of the connection.
 */
export const ETA_BLEND_WINDOW_MS = 5000;
/** The most weight the recent rate is ever given, once the window has elapsed. */
export const ETA_RECENT_WEIGHT_MAX = 0.75;

interface EtaSample {
  bytesDone: number;
  atMs: number;
}

export interface EtaTracker {
  /** Record a progress observation. `atMs` is a wall clock (`Date.now()`). */
  sample(bytesDone: number, atMs: number): void;
  /**
   * Seconds remaining for a transfer of `bytesTotal`, or `null` when there is
   * not yet enough signal — the caller renders "Calculating…" rather than a
   * number it made up.
   *
   * The returned value never falls below {@link MIN_REMAINING_SECONDS} and
   * never exceeds {@link MAX_REMAINING_SECONDS}. It is deliberately *not*
   * monotonic: it may rise again when throughput genuinely drops, and the only
   * thing that keeps it steady is the blend of two rates whose errors decay.
   */
  remainingSeconds(bytesTotal: number): number | null;
}

export function createEtaTracker(alpha: number = DEFAULT_ETA_ALPHA): EtaTracker {
  const samples: EtaSample[] = [];
  // Smoothed throughput in bytes/second; `null` until the second sample.
  let smoothedRate: number | null = null;
  // The transfer's first observation. Kept outside the trimming window on
  // purpose: `bytesDone / elapsed` is only self-correcting if its denominator
  // is the *whole* elapsed time, and `samples[0]` moves forward as the window
  // slides.
  let startedAtMs: number | null = null;
  let startedBytes = 0;

  return {
    sample(bytesDone: number, atMs: number) {
      if (startedAtMs === null) {
        startedAtMs = atMs;
        startedBytes = bytesDone;
      }
      const previous = samples[samples.length - 1];
      if (previous) {
        const dtSeconds = (atMs - previous.atMs) / 1000;
        const deltaBytes = bytesDone - previous.bytesDone;
        // Ignore a zero/negative time step and a backwards byte count. The
        // latter happens when a retried part resets its own counter, and it
        // carries no throughput information.
        if (dtSeconds > 0 && deltaBytes >= 0) {
          const instantRate = deltaBytes / dtSeconds;
          if (smoothedRate === null) {
            smoothedRate = instantRate;
          } else {
            // Time-scaled weight: a sample covering `dt` seconds gets the
            // fraction of the reference weight that `dt` is of the reference
            // step. `1 - (1 - a) ^ (dt / reference)` is the same geometric
            // decay applied over `dt / reference` steps, so a reference-length
            // sample still moves the average by exactly `alpha` — while the
            // 5 ms opening burst between three parallel parts moves it by
            // ~0.5% instead of by 30% of a 1.2 GB/second figure.
            const sampleAlpha =
              1 - Math.pow(1 - alpha, dtSeconds / ETA_REFERENCE_SAMPLE_SECONDS);
            smoothedRate = sampleAlpha * instantRate + (1 - sampleAlpha) * smoothedRate;
          }
        }
      }
      samples.push({ bytesDone, atMs });
      // Only the total span and the sample count matter, so the window can be
      // bounded; an upload reporting every few hundred ms would otherwise grow
      // this array for the whole transfer.
      if (samples.length > 64) samples.shift();
    },

    remainingSeconds(bytesTotal: number): number | null {
      const first = samples[0];
      const last = samples[samples.length - 1];
      if (!first || !last) return null;
      if (samples.length < MIN_ETA_SAMPLES) return null;
      if (last.atMs - first.atMs < MIN_ETA_SPAN_MS) return null;

      const elapsedSeconds = (last.atMs - (startedAtMs ?? first.atMs)) / 1000;
      if (!(elapsedSeconds > 0)) return null;

      // The overall average since the transfer began. A burst at the start is a
      // fixed number of bytes, so its weight here shrinks as `1/elapsed` and
      // this term converges on the connection's real throughput no matter how
      // wrong the opening samples were.
      const overallBytes = last.bytesDone - startedBytes;
      const overallRate = overallBytes > 0 ? overallBytes / elapsedSeconds : 0;
      const recentRate = smoothedRate ?? 0;

      const recentWeight =
        ETA_RECENT_WEIGHT_MAX *
        Math.min(1, (elapsedSeconds * 1000) / ETA_BLEND_WINDOW_MS);
      const rate = recentWeight * recentRate + (1 - recentWeight) * overallRate;

      // A zero or unknown rate cannot be turned into an estimate. A *stalled*
      // transfer keeps a small positive rate (both terms decay toward zero
      // rather than jumping there), so it reports a long but finite time rather
      // than "0 seconds left".
      if (!(rate > 0)) return null;

      const seconds = (bytesTotal - last.bytesDone) / rate;

      // Floor and ceiling. `Math.round` is deliberately applied by `formatEta`,
      // so a fractional 0.4 s is clamped here rather than later.
      return Math.max(MIN_REMAINING_SECONDS, Math.min(MAX_REMAINING_SECONDS, seconds));
    },
  };
}

/**
 * A human phrase for an estimate, e.g. `"12 seconds left"`, `"about 2 min
 * left"`, `"about 1 hr 5 min left"`.
 *
 * `null`/non-finite input reads as "Calculating…" — a wrong number is worse
 * than an honest wait.
 */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "Calculating…";
  }
  const total = Math.max(MIN_REMAINING_SECONDS, Math.round(seconds));

  if (total < 60) return `${total} second${total === 1 ? "" : "s"} left`;

  if (total < 3600) {
    const minutes = Math.max(1, Math.round(total / 60));
    // 59.9 min rounds to 60; carry it rather than print "about 60 min left".
    if (minutes >= 60) return "about 1 hr left";
    return `about ${minutes} min left`;
  }

  const hours = Math.floor(total / 3600);
  let minutes = Math.round((total % 3600) / 60);
  let shownHours = hours;
  if (minutes === 60) {
    shownHours += 1;
    minutes = 0;
  }
  const hourLabel = `${shownHours} hr${shownHours === 1 ? "" : "s"}`;
  return minutes === 0
    ? `about ${hourLabel} left`
    : `about ${hourLabel} ${minutes} min left`;
}

/**
 * A whole-number percentage for `done / total`, clamped to 0–100, or `null`
 * when there is no meaningful denominator (so the bar renders indeterminate).
 *
 * Rounding here rather than in each caller keeps a 4 GB upload's readout from
 * wobbling between 99.6 and 100.
 */
export function formatProgressPercent(done: number, total: number): number | null {
  if (!Number.isFinite(total) || total <= 0) return null;
  const percent = (done / total) * 100;
  if (!Number.isFinite(percent)) return null;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

/**
 * Bytes done for a multipart file, aggregated from its parts.
 *
 * `completedBytes` is the sum of the ranges already PUT; `inFlightLoadedBytes`
 * is the sum of the bytes reported so far by the parts currently being PUT, so
 * cancelling or retrying one part only removes *its* contribution. Clamped to
 * `size` because the two counters are updated independently and can overlap
 * briefly.
 */
export function uploadBytesFromParts(input: {
  completedBytes: number;
  inFlightLoadedBytes: number;
  size: number;
}): number {
  const total = input.completedBytes + input.inFlightLoadedBytes;
  if (!Number.isFinite(total)) return 0;
  return Math.max(0, Math.min(Math.max(0, input.size), total));
}

export interface OverallProgress {
  bytesDone: number;
  bytesTotal: number;
  /** `bytesDone / bytesTotal` as a 0–100 integer, or `null` when empty. */
  percent: number | null;
}

/**
 * The combined progress of several uploads, **weighted by bytes**.
 *
 * Averaging per-file percentages would let a finished 4 KB file cancel out a
 * half-uploaded 4 GB one; summing bytes is the only aggregation that reflects
 * how much work is actually left.
 */
export function combineProgress(
  items: readonly { bytesDone: number; size: number }[],
): OverallProgress {
  let bytesDone = 0;
  let bytesTotal = 0;
  for (const item of items) {
    const size = Number.isFinite(item.size) ? Math.max(0, item.size) : 0;
    const done = Number.isFinite(item.bytesDone) ? Math.max(0, item.bytesDone) : 0;
    bytesTotal += size;
    bytesDone += Math.min(done, size);
  }
  return { bytesDone, bytesTotal, percent: formatProgressPercent(bytesDone, bytesTotal) };
}
