// Regression tests for the background upload store's scoping, persistence and
// reducers.
//
// Context: the conversion job store shipped a cross-account leak because it kept
// one unscoped `localStorage` entry that survived sign-out, and it let a
// "PROCESSING" row outlive the tab that owned it because nothing marked it
// interrupted. Uploads have the same two hazards plus a third of their own — a
// `File` cannot be serialised, so a "resumed" upload whose bytes are gone would
// be a lie. These tests pin all three.

import { describe, expect, it } from "vitest";
import type { KeyValueStore } from "@/jobs/jobStore";
import {
  FINISHED_UPLOAD_RETENTION_MS,
  INTERRUPTED_UPLOAD_MESSAGE,
  LEGACY_UPLOADS_STORAGE_KEY,
  UPLOADS_STORAGE_KEY,
  cancelUpload,
  dismissUpload,
  enqueueUploads,
  failUpload,
  interruptActiveUploads,
  limitRefusal,
  nextUploadsToStart,
  progressTick,
  pruneUploads,
  readStoredUploads,
  reducePartProgress,
  removeStoredUploads,
  requeueUpload,
  serializeUploads,
  succeedUpload,
  uploadStorageKey,
  type UiUpload,
} from "@/lib/uploadStore";

/** In-memory `localStorage` double, mirroring the job store's tests. */
function memoryStore(seed: Record<string, string> = {}): KeyValueStore & { keys: () => string[] } {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
    keys: () => [...data.keys()],
  };
}

function upload(overrides: Partial<UiUpload> = {}): UiUpload {
  return {
    id: "upload-1",
    fileName: "report.pdf",
    size: 1000,
    status: "queued",
    bytesDone: 0,
    folderId: null,
    createdAt: new Date().toISOString(),
    error: null,
    retryable: false,
    etaSeconds: null,
    ...overrides,
  };
}

describe("uploadStorageKey", () => {
  it("scopes the key to the signed-in identity", () => {
    expect(uploadStorageKey(7)).toBe(`${UPLOADS_STORAGE_KEY}:7`);
    expect(uploadStorageKey(8)).toBe(`${UPLOADS_STORAGE_KEY}:8`);
  });

  it("gives anonymous sessions their own key", () => {
    expect(uploadStorageKey(null)).toBe(`${UPLOADS_STORAGE_KEY}:anon`);
    expect(uploadStorageKey(null)).not.toBe(uploadStorageKey(7));
  });

  it("does not collide with the legacy unscoped key", () => {
    expect(uploadStorageKey(7)).not.toBe(LEGACY_UPLOADS_STORAGE_KEY);
    expect(uploadStorageKey(null)).not.toBe(LEGACY_UPLOADS_STORAGE_KEY);
  });
});

describe("readStoredUploads", () => {
  it("returns nothing for a brand-new account", () => {
    const store = memoryStore({
      [uploadStorageKey(1)]: serializeUploads([upload({ id: "from-user-1" })]),
    });
    expect(readStoredUploads(2, store)).toEqual([]);
  });

  it("returns only the requesting identity's own uploads", () => {
    const store = memoryStore({
      [uploadStorageKey(1)]: serializeUploads([upload({ id: "from-user-1" })]),
      [uploadStorageKey(2)]: serializeUploads([upload({ id: "from-user-2" })]),
    });
    expect(readStoredUploads(2, store).map((row) => row.id)).toEqual(["from-user-2"]);
  });

  it("ignores the legacy unscoped cache rather than adopting it", () => {
    const store = memoryStore({
      [LEGACY_UPLOADS_STORAGE_KEY]: serializeUploads([upload({ id: "stale" })]),
    });
    expect(readStoredUploads(99, store)).toEqual([]);
    expect(readStoredUploads(null, store)).toEqual([]);
  });

  it("tolerates missing and corrupt data", () => {
    expect(readStoredUploads(1, memoryStore())).toEqual([]);
    expect(readStoredUploads(1, memoryStore({ [uploadStorageKey(1)]: "{not json" }))).toEqual([]);
    expect(readStoredUploads(1, memoryStore({ [uploadStorageKey(1)]: '{"a":1}' }))).toEqual([]);
  });

  it("drops rows without an id instead of rendering a nameless upload", () => {
    const raw = JSON.stringify([{ fileName: "x.pdf", size: 10 }, { id: "ok", size: 5 }]);
    const rows = readStoredUploads(1, memoryStore({ [uploadStorageKey(1)]: raw }));
    expect(rows.map((row) => row.id)).toEqual(["ok"]);
  });

  it("marks an upload that was in flight when the tab closed as interrupted", () => {
    const store = memoryStore({
      [uploadStorageKey(1)]: serializeUploads([
        upload({ id: "live", status: "uploading", bytesDone: 400 }),
      ]),
    });
    const [row] = readStoredUploads(1, store);
    expect(row.status).toBe("interrupted");
    // The bytes were in memory and are gone, so a retry cannot reuse them.
    expect(row.retryable).toBe(false);
    expect(row.error).toBe(INTERRUPTED_UPLOAD_MESSAGE);
    expect(row.etaSeconds).toBeNull();
  });

  it("leaves a terminal upload's outcome alone", () => {
    const store = memoryStore({
      [uploadStorageKey(1)]: serializeUploads([
        upload({ id: "ok", status: "done", bytesDone: 1000, verified: true }),
        upload({ id: "bad", status: "failed", error: "boom" }),
      ]),
    });
    const rows = readStoredUploads(1, store);
    expect(rows.map((row) => row.status)).toEqual(["done", "failed"]);
    expect(rows[1].error).toBe("boom");
  });

  it("treats an unrecognised stored status as interrupted", () => {
    const raw = JSON.stringify([{ id: "weird", status: "exploded", size: 10 }]);
    expect(readStoredUploads(1, memoryStore({ [uploadStorageKey(1)]: raw }))[0].status).toBe(
      "interrupted",
    );
  });
});

describe("removeStoredUploads", () => {
  it("drops only the named identity's cache", () => {
    const store = memoryStore({
      [uploadStorageKey(1)]: "[]",
      [uploadStorageKey(2)]: "[]",
    });
    removeStoredUploads(1, store);
    expect(store.keys()).toEqual([uploadStorageKey(2)]);
  });
});

describe("serializeUploads", () => {
  it("never writes file bytes to storage", () => {
    // A caller attaching the in-memory `File` for its own convenience must not
    // be able to leak it into a string-only store.
    const withRogueFile = { ...upload(), file: { name: "secret.pdf", size: 1000 } } as UiUpload;
    const json = serializeUploads([withRogueFile]);
    expect(json).not.toContain('"file":');
    expect(json).toContain("report.pdf");
  });

  it("round-trips metadata and clears a meaningless stale estimate", () => {
    const json = serializeUploads([upload({ etaSeconds: 42, uploadId: "srv-1" })]);
    const stored = JSON.parse(json) as Array<Record<string, unknown>>;
    expect(stored[0].etaSeconds).toBeNull();
    const [row] = readStoredUploads(1, memoryStore({ [uploadStorageKey(1)]: json }));
    expect(row.uploadId).toBe("srv-1");
    expect(row.etaSeconds).toBeNull();
  });
});

describe("pruneUploads", () => {
  const now = Date.parse("2026-09-22T12:00:00.000Z");

  it("drops a finished upload once its retention window has passed", () => {
    const old = new Date(now - FINISHED_UPLOAD_RETENTION_MS - 1).toISOString();
    const rows = pruneUploads([upload({ id: "old", status: "done", createdAt: old })], now);
    expect(rows).toEqual([]);
  });

  it("keeps a recent finished upload and any active one, however old", () => {
    const old = new Date(now - FINISHED_UPLOAD_RETENTION_MS - 1).toISOString();
    const rows = pruneUploads(
      [
        upload({ id: "recent", status: "done", createdAt: new Date(now).toISOString() }),
        upload({ id: "active", status: "uploading", createdAt: old }),
      ],
      now,
    );
    expect(rows.map((row) => row.id)).toEqual(["recent", "active"]);
  });

  it("keeps a row whose age cannot be determined rather than guessing", () => {
    const rows = pruneUploads([upload({ id: "unknown", createdAt: "not-a-date" })], now);
    expect(rows.map((row) => row.id)).toEqual(["unknown"]);
  });
});

describe("reducers", () => {
  it("enqueues newest-first", () => {
    const rows = enqueueUploads(
      [upload({ id: "existing" })],
      [upload({ id: "new-a" }), upload({ id: "new-b" })],
    );
    expect(rows.map((row) => row.id)).toEqual(["new-a", "new-b", "existing"]);
  });

  it("applies a byte count, clamped to the file size", () => {
    const rows = progressTick([upload({ size: 100 })], "upload-1", { bytesDone: 250 });
    expect(rows[0].bytesDone).toBe(100);
    expect(progressTick([upload()], "upload-1", { bytesDone: -5 })[0].bytesDone).toBe(0);
    // A row that is not named is untouched.
    expect(progressTick([upload()], "other", { bytesDone: 10 })[0].bytesDone).toBe(0);
  });

  it("updates the estimate only when one is supplied", () => {
    const withEstimate = progressTick([upload({ etaSeconds: 30 })], "upload-1", {
      bytesDone: 10,
    });
    expect(withEstimate[0].etaSeconds).toBe(30);
    const cleared = progressTick(withEstimate, "upload-1", { bytesDone: 20, etaSeconds: null });
    expect(cleared[0].etaSeconds).toBeNull();
  });

  it("aggregates multipart progress, clamping to the file", () => {
    const row = reducePartProgress(upload({ size: 1000 }), {
      completedBytes: 600,
      inFlightLoadedBytes: 100,
    });
    expect(row.bytesDone).toBe(700);
    const overshoot = reducePartProgress(upload({ size: 1000 }), {
      completedBytes: 900,
      inFlightLoadedBytes: 900,
    });
    expect(overshoot.bytesDone).toBe(1000);
  });

  it("marks success only with the server's confirmation", () => {
    const [row] = succeedUpload([upload({ bytesDone: 10, etaSeconds: 5 })], "upload-1");
    expect(row.status).toBe("done");
    expect(row.bytesDone).toBe(1000);
    expect(row.verified).toBe(true);
    expect(row.etaSeconds).toBeNull();
  });

  it("records a failure and whether a retry can reuse the bytes", () => {
    const [retryable] = failUpload([upload()], "upload-1", "network died", true);
    expect(retryable.status).toBe("failed");
    expect(retryable.error).toBe("network died");
    expect(retryable.retryable).toBe(true);
    const [final] = failUpload([upload()], "upload-1", "too large", false);
    expect(final.retryable).toBe(false);
  });

  it("cancels without leaving a retry affordance", () => {
    const [row] = cancelUpload([upload({ status: "uploading" })], "upload-1");
    expect(row.status).toBe("canceled");
    expect(row.error).toBeNull();
    expect(row.retryable).toBe(false);
  });

  it("requeues with the progress reset, since the work restarts", () => {
    const [row] = requeueUpload(
      [upload({ status: "failed", bytesDone: 500, verified: true })],
      "upload-1",
    );
    expect(row.status).toBe("queued");
    expect(row.bytesDone).toBe(0);
    expect(row.verified).toBe(false);
    expect(row.error).toBeNull();
  });

  it("interrupts only the uploads that were still in flight", () => {
    const rows = interruptActiveUploads([
      upload({ id: "a", status: "uploading" }),
      upload({ id: "b", status: "done" }),
      upload({ id: "c", status: "verifying" }),
    ]);
    expect(rows.map((row) => row.status)).toEqual(["interrupted", "done", "interrupted"]);
  });

  it("dismisses exactly one row", () => {
    const rows = dismissUpload([upload({ id: "a" }), upload({ id: "b" })], "a");
    expect(rows.map((row) => row.id)).toEqual(["b"]);
  });
});

describe("limitRefusal", () => {
  const limits = { maxFileSizeBytes: 5_000, availableBytes: 10_000 };

  it("refuses a file over the per-file cap", () => {
    const message = limitRefusal({ name: "big.bin", size: 5_001 }, 0, limits);
    expect(message).toContain("big.bin");
    expect(message).toContain("too large");
  });

  it("accepts a file exactly at the cap", () => {
    expect(limitRefusal({ name: "ok.bin", size: 5_000 }, 0, limits)).toBeNull();
  });

  it("checks a multi-file drop against the total free space, not one file at a time", () => {
    const each = { name: "part.bin", size: 4_000 };
    // Two fit in the 10,000 free bytes; the third tips it over.
    expect(limitRefusal(each, 0, limits)).toBeNull();
    expect(limitRefusal(each, 4_000, limits)).toBeNull();
    const third = limitRefusal(each, 8_000, limits);
    expect(third).toContain("Not enough storage");
    expect(third).toContain("part.bin");
    expect(third).toContain("available");
  });

  it("stays silent when the API does not publish the numbers", () => {
    const unknown = { maxFileSizeBytes: null, availableBytes: null };
    expect(limitRefusal({ name: "anything.bin", size: 999_999_999 }, 0, unknown)).toBeNull();
  });
});

// ---- Queue pump scheduling ----
//
// Regression tests for the deadlock this rule replaced: capacity was computed
// with `isUploadActive`, which counts `queued` rows as in flight, so a drop of
// MAX_CONCURRENT (or more) files computed capacity 0 and started nothing at all
// — permanently, since no state ever changed to re-run the pump.
describe("nextUploadsToStart", () => {
  const MAX = 2;
  const noRunning: ReadonlySet<string> = new Set<string>();

  it("starts a single queued upload immediately", () => {
    expect(nextUploadsToStart([upload({ id: "a" })], noRunning, MAX)).toEqual(["a"]);
  });

  it("starts a full batch instead of deadlocking on its own queue", () => {
    // Newest-first, as `enqueueUploads` stores it.
    const list = [upload({ id: "b" }), upload({ id: "a" })];
    expect(nextUploadsToStart(list, noRunning, MAX)).toEqual(["a", "b"]);
  });

  it("starts at most the available slots and leaves the rest queued", () => {
    const list = ["c", "b", "a"].map((id) => upload({ id }));
    expect(nextUploadsToStart(list, noRunning, MAX)).toEqual(["a", "b"]);
  });

  it("starts a new upload as soon as a running one frees a slot", () => {
    const list = [
      upload({ id: "b" }),
      upload({ id: "a", status: "uploading" }),
    ];
    // One of two slots is taken by the running row, so exactly one starts.
    expect(nextUploadsToStart(list, noRunning, MAX)).toEqual(["b"]);
  });

  it("starts nothing while every slot is busy", () => {
    const list = [
      upload({ id: "c" }),
      upload({ id: "b", status: "uploading" }),
      upload({ id: "a", status: "verifying" }),
    ];
    expect(nextUploadsToStart(list, noRunning, MAX)).toEqual([]);
  });

  it("never starts a row it has already been handed this tick", () => {
    // `start()` is async, so a row it was given is still `queued` in the next
    // render; without the guard the same file would be uploaded twice.
    const list = [upload({ id: "a" })];
    expect(nextUploadsToStart(list, new Set(["a"]), MAX)).toEqual([]);
  });

  it("ignores finished and interrupted rows", () => {
    const list = [
      upload({ id: "d", status: "done" }),
      upload({ id: "c", status: "interrupted" }),
      upload({ id: "b", status: "failed" }),
      upload({ id: "a", status: "queued" }),
    ];
    expect(nextUploadsToStart(list, noRunning, MAX)).toEqual(["a"]);
  });

  it("returns nothing for an empty queue", () => {
    expect(nextUploadsToStart([], noRunning, MAX)).toEqual([]);
  });
});
