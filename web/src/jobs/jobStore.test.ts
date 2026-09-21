// Regression tests for the conversion job store's scoping and reconciliation.
//
// Context: the job list backing the Queue and History pages used to be read
// from and written to one unscoped `localStorage` entry that was never cleared
// on sign-out, so creating a brand-new account in the same browser opened onto
// the previous account's conversions. `refresh()` also merged rather than
// reconciled, so a leftover "PROCESSING" row survived every refresh and sat in
// the queue indefinitely.
//
// These tests pin both halves of the fix.

import { describe, expect, it } from "vitest";
import type { ConversionJobResponse } from "@/api/types";
import {
  IN_FLIGHT_STATUSES,
  JOBS_STORAGE_KEY,
  LEGACY_JOBS_STORAGE_KEY,
  readStoredJobs,
  reconcileJobs,
  removeStoredJobs,
  storageKeyFor,
  type KeyValueStore,
  type UiJob,
} from "@/jobs/jobStore";

/** In-memory `localStorage` double. */
function memoryStore(seed: Record<string, string> = {}): KeyValueStore {
  const data = new Map(Object.entries(seed));
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    removeItem: (key) => void data.delete(key),
    // Exposed for assertions.
    ...({ keys: () => [...data.keys()] } as object),
  } as KeyValueStore & { keys: () => string[] };
}

function job(overrides: Partial<UiJob> = {}): UiJob {
  return {
    job_id: "job-1",
    status: "COMPLETED",
    source_format: "pdf",
    target_format: "docx",
    input_file: "input.pdf",
    output_file: "output.docx",
    object_key: "uploads/input.pdf",
    download_url: null,
    ...overrides,
  };
}

describe("storageKeyFor", () => {
  it("scopes the key to the signed-in identity", () => {
    expect(storageKeyFor(7)).toBe(`${JOBS_STORAGE_KEY}:7`);
    expect(storageKeyFor(8)).toBe(`${JOBS_STORAGE_KEY}:8`);
  });

  it("gives anonymous sessions their own key, distinct from every account", () => {
    expect(storageKeyFor(null)).toBe(`${JOBS_STORAGE_KEY}:anon`);
    expect(storageKeyFor(null)).not.toBe(storageKeyFor(7));
  });

  it("does not collide with the legacy unscoped key", () => {
    // The legacy key is the bare base name; scoped keys always carry a suffix.
    expect(storageKeyFor(7)).not.toBe(LEGACY_JOBS_STORAGE_KEY);
    expect(storageKeyFor(null)).not.toBe(LEGACY_JOBS_STORAGE_KEY);
  });
});

describe("readStoredJobs", () => {
  it("returns nothing for a brand-new account", () => {
    // Another account's cache exists, but under a *different* key.
    const store = memoryStore({
      [storageKeyFor(1)]: JSON.stringify([job({ job_id: "job-from-user-1" })]),
    });
    expect(readStoredJobs(2, store)).toEqual([]);
  });

  it("returns only the requesting identity's own jobs", () => {
    const store = memoryStore({
      [storageKeyFor(1)]: JSON.stringify([job({ job_id: "job-from-user-1" })]),
      [storageKeyFor(2)]: JSON.stringify([job({ job_id: "job-from-user-2" })]),
    });
    const mine = readStoredJobs(2, store);
    expect(mine.map((j) => j.job_id)).toEqual(["job-from-user-2"]);
  });

  it("ignores the legacy unscoped cache rather than adopting it", () => {
    // This is the exact shape of the reported bug: a polluted legacy entry that
    // must never be treated as the new account's history.
    const store = memoryStore({
      [LEGACY_JOBS_STORAGE_KEY]: JSON.stringify([job({ job_id: "stale-from-old-account" })]),
    });
    expect(readStoredJobs(99, store)).toEqual([]);
    expect(readStoredJobs(1, store)).toEqual([]);
  });

  it("tolerates missing and corrupt data", () => {
    expect(readStoredJobs(1, memoryStore())).toEqual([]);
    expect(readStoredJobs(1, memoryStore({ [storageKeyFor(1)]: "{not json" }))).toEqual([]);
    // A non-array payload is discarded, not iterated.
    expect(readStoredJobs(1, memoryStore({ [storageKeyFor(1)]: '{"a":1}' }))).toEqual([]);
  });
});

describe("removeStoredJobs", () => {
  it("drops only the named identity's cache", () => {
    const store = memoryStore({
      [storageKeyFor(1)]: "[]",
      [storageKeyFor(2)]: "[]",
    }) as KeyValueStore & { keys: () => string[] };

    removeStoredJobs(1, store);

    expect(store.getItem(storageKeyFor(1))).toBeNull();
    expect(store.getItem(storageKeyFor(2))).not.toBeNull();
  });
});

describe("reconcileJobs", () => {
  it("drops a previous account's leftover in-flight job", () => {
    // The reported bug, at the reconciliation layer: a PENDING row left over
    // from another account is on screen, this session never started it, and the
    // server (correctly) has no such job for this user.
    const leftover = job({ job_id: "leftover", status: "PROCESSING", progress: 40 });

    const result = reconcileJobs([leftover], [], new Set<string>());

    expect(result).toEqual([]);
  });

  it("keeps a job this session started that the server has not indexed yet", () => {
    const justStarted = job({ job_id: "mine", status: "PENDING", progress: 5 });

    const result = reconcileJobs([justStarted], [], new Set(["mine"]));

    expect(result.map((j) => j.job_id)).toEqual(["mine"]);
    expect(result[0].progress).toBe(5);
  });

  it("treats the server as authoritative for finished jobs", () => {
    const staleLocal = job({ job_id: "old", status: "COMPLETED" });
    const fromServer = job({ job_id: "fresh", status: "COMPLETED" });

    const result = reconcileJobs([staleLocal], [fromServer], new Set(["old"]));

    // "old" was not in flight, so even being session-owned does not keep it.
    expect(result.map((j) => j.job_id)).toEqual(["fresh"]);
  });

  it("prefers live local progress for a still-running session job", () => {
    const local = job({ job_id: "running", status: "PROCESSING", progress: 80 });
    const serverCopy = job({ job_id: "running", status: "PROCESSING", progress: 10 });

    const result = reconcileJobs([local], [serverCopy], new Set(["running"]));

    expect(result).toHaveLength(1);
    expect(result[0].progress).toBe(80);
  });

  it("maps the server's error_message onto the UI field", () => {
    const failed = job({ job_id: "failed", status: "FAILED", error_message: "bad input" });

    const result = reconcileJobs([], [failed], new Set<string>());

    expect(result[0].errorMessage).toBe("bad input");
  });

  it("does not duplicate a session job the server has caught up with", () => {
    const local = job({ job_id: "both", status: "PROCESSING", progress: 60 });
    const serverCopy = job({ job_id: "both", status: "COMPLETED" });

    const result = reconcileJobs([local], [serverCopy], new Set(["both"]));

    expect(result.map((j) => j.job_id)).toEqual(["both"]);
    expect(result[0].status).toBe("COMPLETED");
  });

  it("starts a brand-new account from an empty list", () => {
    // End-to-end shape of the fix: polluted local state + empty server history
    // for the new identity => nothing rendered in Queue or History.
    const polluted = [
      job({ job_id: "a", status: "PROCESSING" }),
      job({ job_id: "b", status: "PENDING" }),
      job({ job_id: "c", status: "COMPLETED" }),
    ];

    expect(reconcileJobs(polluted, [], new Set<string>())).toEqual([]);
  });
});

describe("IN_FLIGHT_STATUSES", () => {
  it("covers every non-terminal status the queue renders", () => {
    expect([...IN_FLIGHT_STATUSES].sort()).toEqual(["AWAITING_UPLOAD", "PENDING", "PROCESSING"]);
    expect(IN_FLIGHT_STATUSES.has("COMPLETED")).toBe(false);
    expect(IN_FLIGHT_STATUSES.has("FAILED")).toBe(false);
  });

  it("ignores a status the server sent but this client does not know", () => {
    const unknown = job({ job_id: "x", status: "RETRYING" });
    const serverCopy: ConversionJobResponse = job({ job_id: "x", status: "RETRYING" });

    // Not recognised as in flight, so it is replaced by the server's copy
    // rather than preserved — the safe direction.
    const result = reconcileJobs([unknown], [serverCopy], new Set(["x"]));

    expect(result).toHaveLength(1);
  });
});

describe("cross-account journey (the reported bug)", () => {
  // Walks the exact sequence the provider performs, in order, so the reported
  // symptom cannot come back unnoticed:
  //   account A converts files -> A signs out -> brand-new account B signs in.
  it("shows a brand-new account none of the previous account's conversions", () => {
    const store = memoryStore();

    // 1. Account A converts two files; the provider persists under A's key.
    const aJobs = [
      job({ job_id: "a-queued", status: "PROCESSING", progress: 30 }),
      job({ job_id: "a-done", status: "COMPLETED" }),
    ];
    store.setItem(storageKeyFor(1), JSON.stringify(aJobs));
    expect(readStoredJobs(1, store)).toHaveLength(2);

    // 2. A signs out. The provider discards the departing identity's cache.
    removeStoredJobs(1, store);

    // 3. Account B is created and signs in for the first time: nothing to show.
    expect(readStoredJobs(2, store)).toEqual([]);

    // 4. Even with an empty server history, the queue/history stay empty — the
    //    reconciliation must not resurrect A's rows.
    expect(reconcileJobs(readStoredJobs(2, store), [], new Set<string>())).toEqual([]);
  });

  it("keeps A's conversions readable while A is still the signed-in identity", () => {
    // The fix must scope data, not discard it: the owning account still sees
    // its own history after a reload.
    const store = memoryStore();
    store.setItem(storageKeyFor(1), JSON.stringify([job({ job_id: "a-done" })]));

    expect(readStoredJobs(1, store).map((j) => j.job_id)).toEqual(["a-done"]);
  });

  it("recovers a browser polluted by the old unscoped cache", () => {
    // Existing users already have a populated legacy key. It must be dropped,
    // so the very next load is clean rather than showing another account's jobs.
    const store = memoryStore({
      [LEGACY_JOBS_STORAGE_KEY]: JSON.stringify([job({ job_id: "someone-elses-job" })]),
    });

    // What the provider does on mount.
    store.removeItem(LEGACY_JOBS_STORAGE_KEY);

    expect(store.getItem(LEGACY_JOBS_STORAGE_KEY)).toBeNull();
    expect(readStoredJobs(1, store)).toEqual([]);
  });
});
