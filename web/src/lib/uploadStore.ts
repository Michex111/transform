/**
 * Pure, DOM-free state helpers behind the background upload store
 * (`UploadsContext`) — the `jobs/jobStore.ts` precedent applied to uploads.
 *
 * Two lessons from the job store are encoded here rather than re-learned:
 *
 *  1. **Every cache entry is scoped to one identity.** A single unscoped
 *     `localStorage` key survives sign-out, so the next account to use the
 *     browser opens onto the previous one's queued work. The pre-scoping key is
 *     discarded, never adopted — its owner is unknown.
 *  2. **A `File` cannot be persisted.** `localStorage` stores strings only, so
 *     the bytes live in memory for the session. Only metadata is written, and
 *     anything that was mid-flight when the tab closed is marked `interrupted`
 *     on the next load: a "resumed" upload whose bytes are gone is a lie, and
 *     re-uploading without asking would be worse.
 */

import type { UploadMode } from "@/api/types";
import { formatBytes } from "@/lib/format";
import { type KeyValueStore, storageKeyFor as scopedStorageKey } from "@/jobs/jobStore";
import {
  isUploadActive,
  isUploadRunning,
  uploadBytesFromParts,
  type UploadStatus,
} from "@/lib/uploadEta";

/** Base key. Every cache entry is suffixed with the identity that owns it. */
export const UPLOADS_STORAGE_KEY = "transform_uploads";

/**
 * The unscoped key a pre-scoping build would have used. It held whichever
 * account used the browser last, so it is discarded on sight rather than
 * adopted — see the job store's regression test for the bug this prevents.
 */
export const LEGACY_UPLOADS_STORAGE_KEY = UPLOADS_STORAGE_KEY;

/** How long a finished upload stays in the persisted cache. */
export const FINISHED_UPLOAD_RETENTION_MS = 24 * 60 * 60 * 1000;

/** The message an upload that was in flight when the tab closed carries. */
export const INTERRUPTED_UPLOAD_MESSAGE =
  "Upload was interrupted when the page closed. Select the file again to restart it.";

/** A client-side upload record. The `File`/`Blob` is never part of this. */
export interface UiUpload {
  /** Client-side id; exists before the server session does. */
  id: string;
  fileName: string;
  size: number;
  status: UploadStatus;
  bytesDone: number;
  folderId: string | null;
  createdAt: string;
  /** Failure text, or `null` while there is nothing to report. */
  error?: string | null;
  /** Whether a retry can reuse the in-memory file. */
  retryable?: boolean;
  /** Latest smoothed estimate, fed by the ETA tracker in the engine. */
  etaSeconds?: number | null;
  /** Server session id, set once the session exists. */
  uploadId?: string | null;
  uploadMode?: UploadMode | null;
  partSizeBytes?: number | null;
  partCount?: number | null;
  /** True once the server's verify call confirmed the object. */
  verified?: boolean;
}

/** The `localStorage` key holding exactly one identity's upload cache. */
export function uploadStorageKey(userId: number | null): string {
  // Shares the job store's scoping rule rather than duplicating it, so the two
  // caches cannot drift on what an identity key looks like.
  return scopedStorageKey(userId, UPLOADS_STORAGE_KEY);
}

/** The metadata persisted per upload. Explicit, so a `File` can never sneak in. */
function toStored(upload: UiUpload): UiUpload {
  return {
    id: upload.id,
    fileName: upload.fileName,
    size: upload.size,
    status: upload.status,
    bytesDone: upload.bytesDone,
    folderId: upload.folderId,
    createdAt: upload.createdAt,
    error: upload.error ?? null,
    retryable: upload.retryable ?? false,
    etaSeconds: null, // a stale estimate for a non-running upload is meaningless
    uploadId: upload.uploadId ?? null,
    uploadMode: upload.uploadMode ?? null,
    partSizeBytes: upload.partSizeBytes ?? null,
    partCount: upload.partCount ?? null,
    verified: upload.verified ?? false,
  };
}

/** Serialise the list without ever writing file bytes. */
export function serializeUploads(uploads: readonly UiUpload[]): string {
  return JSON.stringify(uploads.map(toStored));
}

/**
 * Coerce one parsed row into a `UiUpload`.
 *
 * Statuses and sizes are validated because this reads user-writable storage: a
 * corrupt or hand-edited entry must degrade to a harmless record rather than
 * crash the dock or report a nonsense size.
 */
function fromStored(value: unknown): UiUpload | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== "string" || !row.id) return null;
  const status: UploadStatus =
    typeof row.status === "string" && STATUS_VALUES.has(row.status as UploadStatus)
      ? (row.status as UploadStatus)
      : "interrupted";
  const size = Number.isFinite(row.size) ? Math.max(0, row.size as number) : 0;
  return {
    id: row.id,
    fileName: typeof row.fileName === "string" ? row.fileName : "",
    size,
    status,
    bytesDone: Number.isFinite(row.bytesDone)
      ? Math.max(0, Math.min(size, row.bytesDone as number))
      : 0,
    folderId: typeof row.folderId === "string" ? row.folderId : null,
    createdAt: typeof row.createdAt === "string" ? row.createdAt : new Date().toISOString(),
    error: typeof row.error === "string" ? row.error : null,
    retryable: row.retryable === true,
    etaSeconds: null,
    uploadId: typeof row.uploadId === "string" ? row.uploadId : null,
    uploadMode: row.uploadMode === "multipart" ? "multipart" : row.uploadMode === "single" ? "single" : null,
    partSizeBytes: Number.isFinite(row.partSizeBytes) ? (row.partSizeBytes as number) : null,
    partCount: Number.isFinite(row.partCount) ? (row.partCount as number) : null,
    verified: row.verified === true,
  };
}

const STATUS_VALUES: ReadonlySet<UploadStatus> = new Set<UploadStatus>([
  "queued",
  "uploading",
  "verifying",
  "done",
  "failed",
  "canceled",
  "interrupted",
]);

/** Read one identity's cached uploads, tolerating missing/corrupt data. */
export function readStoredUploads(
  userId: number | null,
  store: KeyValueStore,
  now: number = Date.now(),
): UiUpload[] {
  try {
    const raw = store.getItem(uploadStorageKey(userId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const rows = parsed
      .map(fromStored)
      .filter((row): row is UiUpload => row !== null);
    // Anything that was in flight when the tab died has no bytes left, so it
    // becomes an explicit interruption instead of an "in progress" row that
    // would never move again.
    return pruneUploads(interruptActiveUploads(rows), now);
  } catch {
    return [];
  }
}

/** Drop one identity's cache, so it cannot outlive its session. */
export function removeStoredUploads(userId: number | null, store: KeyValueStore): void {
  try {
    store.removeItem(uploadStorageKey(userId));
  } catch {
    /* ignore */
  }
}

/**
 * Discard terminal rows older than {@link FINISHED_UPLOAD_RETENTION_MS}.
 *
 * Applied on hydration only, never while the store is live: a row disappearing
 * from under the user because it aged out mid-session would be worse than a
 * slightly longer list.
 */
export function pruneUploads(uploads: readonly UiUpload[], now: number): UiUpload[] {
  return uploads.filter((upload) => {
    if (isUploadActive(upload.status)) return true;
    const createdAt = Date.parse(upload.createdAt);
    if (!Number.isFinite(createdAt)) return true; // unknown age: keep, don't guess
    return now - createdAt < FINISHED_UPLOAD_RETENTION_MS;
  });
}

// ---- Scheduling ----

/**
 * Which queued uploads the pump may start right now, in the order to start them.
 *
 * The pump's whole decision lives here so it can be unit-tested without a
 * browser (the repo has no DOM test environment), because the bug this replaced
 * was invisible to every other kind of test: it computed capacity as
 * `max − (rows where isUploadActive)`, and `isUploadActive` counts `queued` rows
 * as in flight. With `MAX_CONCURRENT_FILE_UPLOADS = 2`, a 2-file drop therefore
 * computed `2 − 2 = 0` and returned immediately — forever, because nothing ever
 * changed state to re-run the effect. Both files sat at "Waiting to start" and
 * not one request was made. Any drop of ≥ `max` files was dead on arrival.
 *
 * Capacity now counts only rows that are genuinely running (`isUploadRunning`),
 * so waiting rows can never consume the slots they are waiting for.
 *
 * `running` is the set of transfers the caller has already started this tick:
 * `start()` is asynchronous, so a row it was handed is still `queued` in the
 * next render, and without this guard the very next pump run would start it a
 * second time.
 *
 * The list is newest-first (see `enqueueUploads`), so it is walked in reverse:
 * a multi-file drop then starts in the order the files were picked — a FIFO
 * queue, which is what makes the order predictable.
 */
export function nextUploadsToStart(
  uploads: readonly { id: string; status: string }[],
  running: ReadonlySet<string>,
  maxConcurrent: number,
): string[] {
  let capacity =
    maxConcurrent - uploads.filter((upload) => isUploadRunning(upload.status as UploadStatus)).length;
  if (capacity <= 0) return [];

  const toStart: string[] = [];
  for (const upload of [...uploads].reverse()) {
    if (capacity <= 0) break;
    if (upload.status !== "queued" || running.has(upload.id)) continue;
    toStart.push(upload.id);
    capacity -= 1;
  }
  return toStart;
}

// ---- Reducers ----
//
// Pure list transitions, so the rules the dock depends on are testable without
// a browser. Every one preserves the order of the list it is given.

/** Add newly picked uploads to the front (newest first). */
export function enqueueUploads(
  uploads: readonly UiUpload[],
  incoming: readonly UiUpload[],
): UiUpload[] {
  return [...incoming, ...uploads];
}

/** Apply a whole-file byte count (single-PUT progress, or an aggregate). */
export function progressTick(
  uploads: readonly UiUpload[],
  id: string,
  update: { bytesDone: number; etaSeconds?: number | null },
): UiUpload[] {
  return uploads.map((upload) =>
    upload.id === id ? applyProgress(upload, update.bytesDone, update.etaSeconds) : upload,
  );
}

/** Apply multipart aggregates from the engine's part-level counters. */
export function reducePartProgress(
  upload: UiUpload,
  parts: { completedBytes: number; inFlightLoadedBytes: number },
  etaSeconds?: number | null,
): UiUpload {
  return applyProgress(
    upload,
    uploadBytesFromParts({ ...parts, size: upload.size }),
    etaSeconds,
  );
}

function applyProgress(
  upload: UiUpload,
  bytesDone: number,
  etaSeconds?: number | null,
): UiUpload {
  const clamped = Number.isFinite(bytesDone)
    ? Math.max(0, Math.min(upload.size, bytesDone))
    : upload.bytesDone;
  return {
    ...upload,
    bytesDone: clamped,
    // Never regress to "Calculating…" from a real estimate: an absent value
    // only clears the field when the caller explicitly sends `null`.
    etaSeconds: etaSeconds === undefined ? (upload.etaSeconds ?? null) : etaSeconds,
  };
}

/** Mark an upload finished. Only the server's verify may call this. */
export function succeedUpload(uploads: readonly UiUpload[], id: string): UiUpload[] {
  return uploads.map((upload) =>
    upload.id === id
      ? {
          ...upload,
          status: "done",
          bytesDone: upload.size,
          etaSeconds: null,
          error: null,
          retryable: false,
          verified: true,
        }
      : upload,
  );
}

/** Mark an upload failed, recording whether a retry can reuse the file. */
export function failUpload(
  uploads: readonly UiUpload[],
  id: string,
  message: string,
  retryable: boolean,
): UiUpload[] {
  return uploads.map((upload) =>
    upload.id === id
      ? { ...upload, status: "failed", etaSeconds: null, error: message, retryable }
      : upload,
  );
}

/** Mark an upload cancelled by the user. */
export function cancelUpload(uploads: readonly UiUpload[], id: string): UiUpload[] {
  return uploads.map((upload) =>
    upload.id === id
      ? { ...upload, status: "canceled", etaSeconds: null, error: null, retryable: false }
      : upload,
  );
}

/**
 * Move a failed/cancelled upload back to the queue.
 *
 * The byte count resets: the engine starts a fresh session, so showing the old
 * progress would claim work that has to be redone.
 */
export function requeueUpload(uploads: readonly UiUpload[], id: string): UiUpload[] {
  return uploads.map((upload) =>
    upload.id === id
      ? {
          ...upload,
          status: "queued",
          bytesDone: 0,
          etaSeconds: null,
          error: null,
          retryable: false,
          verified: false,
        }
      : upload,
  );
}

/**
 * Mark everything still in flight as interrupted — the hydrate-time step.
 *
 * `retryable` is `false` on purpose: the bytes were in memory and are gone, so
 * the only honest recovery is for the user to pick the file again.
 */
export function interruptActiveUploads(uploads: readonly UiUpload[]): UiUpload[] {
  return uploads.map((upload) =>
    isUploadActive(upload.status)
      ? {
          ...upload,
          status: "interrupted",
          etaSeconds: null,
          error: INTERRUPTED_UPLOAD_MESSAGE,
          retryable: false,
        }
      : upload,
  );
}

/** Remove an upload from the list entirely. */
export function dismissUpload(uploads: readonly UiUpload[], id: string): UiUpload[] {
  return uploads.filter((upload) => upload.id !== id);
}

// ---- Limits ----

/**
 * The numbers the client pre-check compares against.
 *
 * `null` means the running API does not publish them; the check is then skipped
 * and the server's 413 is the only gate.
 */
export interface UploadLimits {
  maxFileSizeBytes: number | null;
  availableBytes: number | null;
}

/**
 * Why `file` cannot be uploaded, or `null` if the client sees no objection.
 *
 * This is **advisory UX only** — it exists so an over-quota file is refused
 * instantly instead of after a long upload, using the same numbers the
 * Dashboard's storage card displays (the server's `storage_stats`). The
 * server's 413 stays authoritative and its message is what gets rendered, so a
 * stale dashboard response can only cause a wasted round trip, never a wrong
 * verdict.
 *
 * `projectedBytes` is the total size of the files already accepted in this
 * batch, so a multi-file drop is checked cumulatively against the free space
 * rather than file-by-file.
 */
export function limitRefusal(
  file: { name: string; size: number },
  projectedBytes: number,
  limits: UploadLimits,
): string | null {
  const { maxFileSizeBytes, availableBytes } = limits;
  if (maxFileSizeBytes !== null && maxFileSizeBytes > 0 && file.size > maxFileSizeBytes) {
    return `"${file.name}" is too large. The maximum upload size is ${formatBytes(maxFileSizeBytes)}.`;
  }
  if (availableBytes !== null && projectedBytes + file.size > availableBytes) {
    const free = Math.max(0, availableBytes - projectedBytes);
    return `Not enough storage for "${file.name}". ${formatBytes(free)} available.`;
  }
  return null;
}
