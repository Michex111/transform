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
}

/** Base key. Every cache entry is suffixed with the identity that owns it. */
export const JOBS_STORAGE_KEY = "transform_jobs";

/**
 * The pre-scoping key. It held whichever account used the browser last, so its
 * owner is unknown and it is discarded rather than adopted.
 */
export const LEGACY_JOBS_STORAGE_KEY = JOBS_STORAGE_KEY;

/** Statuses that mean a job is still running (so may not be in history yet). */
export const IN_FLIGHT_STATUSES: ReadonlySet<string> = new Set([
  "PENDING",
  "PROCESSING",
  "AWAITING_UPLOAD",
]);

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
  const inFlight = new Map(
    previous
      .filter((job) => sessionJobIds.has(job.job_id) && IN_FLIGHT_STATUSES.has(job.status))
      .map((job) => [job.job_id, job] as const),
  );

  const reconciled = serverJobs.map((job) => {
    const local = inFlight.get(job.job_id);
    return local
      ? { ...local, ...job, progress: local.progress }
      : { ...job, errorMessage: job.error_message ?? undefined };
  });

  const serverIds = new Set(serverJobs.map((job) => job.job_id));
  const notYetIndexed = [...inFlight.values()].filter((job) => !serverIds.has(job.job_id));

  // Jobs too new for the server's list first; the rest newest-first, as returned.
  return [...notYetIndexed, ...reconciled];
}
