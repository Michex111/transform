/**
 * Pure, DOM-free state helpers behind the conversion job store
 * (`JobsContext`).
 *
 * These live apart from the React provider because they encode the rules that
 * decide *whose* conversions the UI may show. Keeping them pure makes those
 * rules unit-testable without a browser — they previously could not be tested,
 * which is how a cross-account leak shipped.
 */

import type { ConversionJobResponse } from "@/api/types";

/** A client-side job with upload/progress augmentation. */
export interface UiJob extends ConversionJobResponse {
  fileName?: string;
  progress?: number;
  createdAt?: string;
  /** Error message from the SSE stream for failed conversions. */
  errorMessage?: string;
  /**
   * When this session observed the job reach a terminal state (set from the
   * terminal SSE event — completed *or* failed).
   *
   * Both `createdAt` and the server's `created_at` are *start* times, and the
   * Convert page's "recently finished" list is about finishing: a job whose start
   * is 25 minutes outside the window must still count as recent when it finished
   * a minute ago (a 25-minute conversion is exactly when the user is waiting to
   * see the result). The server has no such timestamp for the SPA to read, so a
   * job that finished in a previous session falls back to its start time — see
   * `recentFinishedJobs`.
   */
  finishedAt?: string;
}

/** Base key. Every cache entry is suffixed with the identity that owns it. */
export const JOBS_STORAGE_KEY = "transform_jobs";

/**
 * The pre-scoping key. It held whichever account used the browser last, so its
 * owner is unknown and it is discarded rather than adopted.
 */
export const LEGACY_JOBS_STORAGE_KEY = JOBS_STORAGE_KEY;

/** How long a finished conversion stays in the Convert page's "recently finished" list. */
export const RECENT_FINISHED_WINDOW_MS = 30 * 60_000;

/**
 * The `localStorage` base key holding when one identity last cleared the
 * Convert queue's recently-completed section.
 *
 * A timestamp, deliberately not a list of job ids: a job that finishes two
 * seconds after a clear must still show up (it is newer than the marker), and a
 * list of ids would grow without bound in a 5 MB-localStorage browser.
 */
export const QUEUE_CLEARED_STORAGE_KEY = "transform_queue_cleared";

/** Statuses that mean a job is still running (so may not be in history yet). */
export const IN_FLIGHT_STATUSES: ReadonlySet<string> = new Set([
  "PENDING",
  "PROCESSING",
  "AWAITING_UPLOAD",
]);

/**
 * The terminal statuses the Convert page reports, because both are actionable:
 * a completed job can be downloaded or filed into the drive, and a failed one can
 * be retried from where the user is standing instead of sending them to History.
 */
export const FINISHED_STATUSES: ReadonlySet<string> = new Set(["COMPLETED", "FAILED"]);

/** Minimal storage surface, so callers/tests can inject a double. */
export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/**
 * The `localStorage` key holding exactly one identity's job cache.
 *
 * Scoping by identity is what stops a brand-new account from opening onto the
 * previous account's queue and history on a shared browser.
 */
export function storageKeyFor(
  userId: number | null,
  baseKey: string = JOBS_STORAGE_KEY,
): string {
  return `${baseKey}:${userId ?? "anon"}`;
}

/** Read one identity's cached jobs, tolerating missing/corrupt data. */
export function readStoredJobs(
  userId: number | null,
  store: KeyValueStore,
  baseKey: string = JOBS_STORAGE_KEY,
): UiJob[] {
  try {
    const raw = store.getItem(storageKeyFor(userId, baseKey));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as UiJob[]) : [];
  } catch {
    return [];
  }
}

/** Drop one identity's cache, so it cannot outlive its session. */
export function removeStoredJobs(
  userId: number | null,
  store: KeyValueStore,
  baseKey: string = JOBS_STORAGE_KEY,
): void {
  try {
    store.removeItem(storageKeyFor(userId, baseKey));
  } catch {
    /* ignore */
  }
}

/**
 * Read when this identity last cleared the Convert queue; null when never or
 * garbage.
 *
 * It lives under its own identity-scoped key (not the jobs key) for the same
 * reason the jobs cache is scoped: a marker left by one account must never hide
 * another account's completions on a shared browser. An unparsable value reads
 * as `null` ("never cleared") rather than as "cleared everything", because the
 * safe failure is showing a job the user has already seen, not hiding new work.
 */
export function readQueueClearedAt(
  userId: number | null,
  store: KeyValueStore,
  baseKey: string = QUEUE_CLEARED_STORAGE_KEY,
): string | null {
  try {
    const raw: unknown = store.getItem(storageKeyFor(userId, baseKey));
    if (typeof raw !== "string" || raw === "" || Number.isNaN(Date.parse(raw))) {
      return null;
    }
    return raw;
  } catch {
    return null;
  }
}

/**
 * Persist the clear marker (an ISO string) for this identity.
 *
 * Written to its own key per identity — never merged into the jobs cache, whose
 * shape and lifecycle are unrelated.
 */
export function markQueueCleared(
  userId: number | null,
  at: string,
  store: KeyValueStore,
  baseKey: string = QUEUE_CLEARED_STORAGE_KEY,
): void {
  try {
    store.setItem(storageKeyFor(userId, baseKey), at);
  } catch {
    /* ignore quota */
  }
}

/** Anything carrying a status, so these predicates accept jobs and server rows. */
interface StatusBearing {
  status: string;
}

/**
 * True while a conversion is still in progress — i.e. it belongs in the Queue.
 *
 * The Queue is a *live* view of work in flight. A finished conversion leaves it
 * and is reported by History instead, which is where its outcome (and its token
 * cost) is shown. `AWAITING_UPLOAD` counts as active: the job exists and is
 * waiting on its input, so it must not vanish from the queue.
 */
export function isActiveJob(job: StatusBearing): boolean {
  return IN_FLIGHT_STATUSES.has(job.status);
}

/** The still-in-progress subset of `jobs`, order preserved. */
export function activeJobs<T extends StatusBearing>(jobs: readonly T[]): T[] {
  return jobs.filter(isActiveJob);
}

/**
 * Reduce a *transport* failure on a job's SSE stream (dropped connection, proxy
 * hiccup, redeploy, offline tab) into the job state to show.
 *
 * A broken stream says nothing about the job itself, so the last known status is
 * kept: marking it `FAILED` was a lie that invited the user to re-run a job that
 * was still converting (double work, double credits). Only the server's own
 * terminal event may move a job to a terminal state.
 *
 * `resubscribe` is returned rather than applied so the caller can forget the
 * dead subscription: leaving it registered made the "already subscribed" guard
 * permanent, so the row never updated again — not even after a `refresh()` had
 * restored the real server status.
 */
export function reduceStreamError<T extends UiJob>(
  job: T,
): { job: T; resubscribe: boolean } {
  return { job, resubscribe: true };
}

/**
 * The progress percentage to render for a job, or `null` when there is no real
 * value to show — in which case the bar renders as indeterminate.
 *
 * The worker reports progress in its SSE events only, so a job restored from
 * cache or listed before its first event has none; the pages used to invent
 * `45` for such rows, which `aria-valuenow` then announced to assistive tech as
 * a fact. A completed job is genuinely at 100%, which is not an invention.
 */
export function jobProgress(job: { status: string; progress?: number }): number | null {
  if (typeof job.progress === "number" && Number.isFinite(job.progress)) {
    return job.progress;
  }
  return job.status === "COMPLETED" ? 100 : null;
}

/**
 * Whether a job should display its token (credit) cost.
 *
 * Tokens are charged only for a *successful* conversion — the worker deducts
 * them after the output is uploaded — so a failed or in-flight job has nothing
 * to report. Single source of truth for the Queue and History tables, which
 * previously duplicated (and so could drift on) this rule.
 */
export function showsCreditsUsed(
  job: StatusBearing & { credits_used?: number | null },
): boolean {
  return job.status === "COMPLETED" && (job.credits_used ?? 0) > 0;
}

/**
 * When a job was created, from whichever source actually has it.
 *
 * Two sources, and a row can have either: `createdAt` is stamped by this
 * browser when a conversion is started here, while `created_at` is the server's
 * row timestamp and is what a history list loaded from the API carries. Every
 * date display and date sort goes through here, because reading only the
 * client-side one is what made the Created column show "—" for every row
 * restored from the server.
 */
export function jobCreatedAt(job: {
  createdAt?: string;
  created_at?: string | null;
}): string | undefined {
  return job.createdAt ?? job.created_at ?? undefined;
}

/** Parse an ISO timestamp, tolerating null/undefined/garbage. */
function parseTime(value: string | null | undefined): number | null {
  if (typeof value !== "string" || value === "") return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * The finished conversions the Convert page shows under "Recently finished".
 *
 * The rules, in full:
 *
 * - Only a terminal status: `COMPLETED` or `FAILED` (`FINISHED_STATUSES`). An
 *   in-flight job is already listed under Active and must not appear twice.
 *   Failures belong here because they are actionable from this page: the row
 *   carries the error and a Retry, so a failed conversion does not force a trip
 *   to History to find out what happened.
 * - "When it finished" is `finishedAt ?? jobCreatedAt(job)` — `finishedAt` is
 *   stamped when this session watches the terminal SSE event (for a failure as
 *   well as a success), and the server timestamp is only ever a *start* time. A
 *   job with neither timestamp is excluded: it cannot be placed in time, so
 *   claiming it is "recent" would be a guess.
 * - Inside the window when `now - finishedAt < RECENT_FINISHED_WINDOW_MS`. A
 *   timestamp in the FUTURE (clock skew between the browser and the server)
 *   counts as recent: hiding a conversion the user just watched finish is the
 *   worse error, and the row is still honest about what it is.
 * - `clearedBefore` is the ISO marker written by the card's Clear control; a job
 *   whose `finishedAt` is `<= clearedBefore` is excluded. A job that finishes
 *   afterwards is newer than the marker and shows up again, which is exactly
 *   why this is a timestamp and not a list of ids. An unparsable `clearedBefore`
 *   is ignored — never treated as "clear everything".
 * - Newest first, capped at `limit` (default 5).
 */
export function recentFinishedJobs<T extends UiJob>(
  jobs: readonly T[],
  options: { now: number; clearedBefore?: string | null; limit?: number },
): T[] {
  const { now, clearedBefore, limit = 5 } = options;
  const clearedBeforeMs = parseTime(clearedBefore);
  const cap = Number.isFinite(limit) ? Math.max(0, Math.trunc(limit)) : 0;

  return jobs
    .filter((job) => FINISHED_STATUSES.has(job.status))
    .map((job) => ({ job, finishedAt: parseTime(job.finishedAt ?? jobCreatedAt(job)) }))
    .filter(
      (row): row is { job: T; finishedAt: number } =>
        row.finishedAt !== null &&
        // Future timestamps fall through here too (the difference is negative).
        now - row.finishedAt < RECENT_FINISHED_WINDOW_MS &&
        (clearedBeforeMs === null || row.finishedAt > clearedBeforeMs),
    )
    .sort((a, b) => b.finishedAt - a.finishedAt)
    .slice(0, cap)
    .map((row) => row.job);
}

/**
 * Whether a row shows the progress bar.
 *
 * - `COMPLETED`: never, at any width. The bar is at 100% and the "Ready" badge
 *   already says so, and on a finished row the width is worth more to the
 *   controls — dropping it is what lets the row fit on one line.
 * - `FAILED`: on a desktop row only (`md` and up — the same breakpoint at which
 *   the row's inline retry and error controls appear). There it keeps the row's
 *   column rhythm beside them; on a phone the row has no space for it, and a bar
 *   that will never move again is noise when the "Failed" badge has already said
 *   what happened.
 * - Still in flight: always. The bar is the only live signal of progress, and
 *   the Queue exists to show work moving.
 */
export function showsProgressBar(
  job: StatusBearing,
  options: { narrow: boolean },
): boolean {
  if (job.status === "COMPLETED") return false;
  if (job.status === "FAILED") return !options.narrow;
  return true;
}

/**
 * Reconcile the on-screen list against the server's history for this identity.
 *
 * The server is authoritative, so anything it does not return is dropped —
 * including a leftover in-flight row that belonged to a previously signed-in
 * account (merging unconditionally was what let such a row survive every
 * refresh and sit in the queue forever). The single exception is a job started
 * during *this* session and still in flight: it may be too new to appear in the
 * response yet, so it is kept along with its live progress, which is ahead of
 * the server's copy.
 */
export function reconcileJobs(
  previous: UiJob[],
  serverJobs: ConversionJobResponse[],
  sessionJobIds: ReadonlySet<string>,
): UiJob[] {
  // Session jobs, in flight or not: the merge below wants this session's
  // observations about a job the server also knows about.
  const sessionJobs = new Map(
    previous
      .filter((job) => sessionJobIds.has(job.job_id))
      .map((job) => [job.job_id, job] as const),
  );

  const reconciled = serverJobs.map((job) => {
    const local = sessionJobs.get(job.job_id);
    if (!local) return { ...job, errorMessage: job.error_message ?? undefined };
    return {
      ...local,
      ...job,
      // The server's copy is authoritative, but these two are things it never
      // sends: the live progress from the SSE stream (which the server's list may
      // predate) and when this session saw the job reach its terminal state.
      // Preserving the latter is what stops a long conversion from vanishing
      // from the Convert page's list on the next refresh — the row would
      // otherwise fall back to its *start* time and read as older than the
      // window. Only `inFlight` below is restricted to unfinished jobs; this
      // map is not, so a job that has since completed keeps its finish stamp.
      progress: local.progress,
      finishedAt: local.finishedAt,
    };
  });

  // Jobs too new for the server's list. Restricted to still-running rows: a
  // terminal job missing from the response has genuinely gone (deleted
  // history), and re-adding it would resurrect a row the user removed.
  const inFlight = new Map(
    previous
      .filter((job) => sessionJobIds.has(job.job_id) && IN_FLIGHT_STATUSES.has(job.status))
      .map((job) => [job.job_id, job] as const),
  );
  const serverIds = new Set(serverJobs.map((job) => job.job_id));
  const notYetIndexed = [...inFlight.values()].filter((job) => !serverIds.has(job.job_id));

  // Jobs too new for the server's list first; the rest newest-first, as returned.
  return [...notYetIndexed, ...reconciled];
}
