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
  FINISHED_STATUSES,
  IN_FLIGHT_STATUSES,
  JOBS_STORAGE_KEY,
  LEGACY_JOBS_STORAGE_KEY,
  QUEUE_CLEARED_STORAGE_KEY,
  RECENT_FINISHED_WINDOW_MS,
  activeJobs,
  isActiveJob,
  jobCreatedAt,
  jobProgress,
  markQueueCleared,
  readQueueClearedAt,
  readStoredJobs,
  recentFinishedJobs,
  reconcileJobs,
  reduceStreamError,
  removeStoredJobs,
  showsCreditsUsed,
  showsProgressBar,
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

describe("isActiveJob / activeJobs", () => {
  it("treats in-progress statuses as active", () => {
    for (const status of ["PENDING", "PROCESSING", "AWAITING_UPLOAD"]) {
      expect(isActiveJob({ status })).toBe(true);
    }
  });

  it("treats finished and unknown statuses as inactive", () => {
    for (const status of ["COMPLETED", "FAILED", "RETRYING", ""]) {
      expect(isActiveJob({ status })).toBe(false);
    }
  });

  it("keeps only the in-progress jobs, in order", () => {
    const list = [
      job({ job_id: "done", status: "COMPLETED" }),
      job({ job_id: "running", status: "PROCESSING" }),
      job({ job_id: "broke", status: "FAILED" }),
      job({ job_id: "queued", status: "PENDING" }),
      job({ job_id: "uploading", status: "AWAITING_UPLOAD" }),
    ];

    expect(activeJobs(list).map((j) => j.job_id)).toEqual(["running", "queued", "uploading"]);
  });

  it("empties the queue once every conversion has finished", () => {
    const list = [
      job({ job_id: "a", status: "COMPLETED" }),
      job({ job_id: "b", status: "FAILED" }),
    ];

    expect(activeJobs(list)).toEqual([]);
  });

  it("does not mutate the list it filters", () => {
    const list = [
      job({ job_id: "a", status: "COMPLETED" }),
      job({ job_id: "b", status: "PENDING" }),
    ];

    activeJobs(list);

    expect(list).toHaveLength(2);
  });
});

describe("showsCreditsUsed", () => {
  it("reports tokens for a completed conversion that consumed them", () => {
    expect(showsCreditsUsed(job({ status: "COMPLETED", credits_used: 5 }))).toBe(true);
  });

  it("hides tokens for a completed conversion that used none", () => {
    expect(showsCreditsUsed(job({ status: "COMPLETED", credits_used: 0 }))).toBe(false);
    expect(showsCreditsUsed(job({ status: "COMPLETED" }))).toBe(false);
    // Tolerates an explicit null from the API.
    expect(showsCreditsUsed({ status: "COMPLETED", credits_used: null })).toBe(false);
  });

  it("hides tokens for a failed conversion, which is never charged", () => {
    // The worker deducts credits only after the output is uploaded, so a failed
    // job must not report a cost even if a stale value is present.
    expect(showsCreditsUsed(job({ status: "FAILED", credits_used: 7 }))).toBe(false);
  });

  it("hides tokens while a conversion is still running", () => {
    for (const status of ["PENDING", "PROCESSING", "AWAITING_UPLOAD"]) {
      expect(showsCreditsUsed(job({ status, credits_used: 3 }))).toBe(false);
    }
  });
});

describe("queue vs history split", () => {
  // One mixed list, as the history endpoint returns it, viewed through both
  // pages' rules: the queue keeps only live work, history reports token cost.
  const mixed = [
    job({ job_id: "done", status: "COMPLETED", credits_used: 4 }),
    job({ job_id: "running", status: "PROCESSING", progress: 50 }),
    job({ job_id: "queued", status: "PENDING" }),
    job({ job_id: "broke", status: "FAILED" }),
  ];

  it("queue shows only the active conversions", () => {
    expect(activeJobs(mixed).map((j) => j.job_id)).toEqual(["running", "queued"]);
  });

  it("history reports tokens for the completed conversion", () => {
    const billed = mixed.filter(showsCreditsUsed);

    expect(billed.map((j) => j.job_id)).toEqual(["done"]);
    expect(billed[0].credits_used).toBe(4);
  });
});

describe("reduceStreamError", () => {
  // A dropped SSE stream (proxy hiccup, redeploy, offline tab) used to be
  // rendered as a FAILED job, inviting the user to re-run a conversion that was
  // still running (double work, double credits), and the dead subscription was
  // left registered so the row never updated again — not even after a refresh
  // had restored the real server status.
  it("keeps the last known status instead of inventing a failure", () => {
    const running = job({ job_id: "running", status: "PROCESSING", progress: 61 });

    const { job: next } = reduceStreamError(running);

    expect(next.status).toBe("PROCESSING");
    expect(next.status).not.toBe("FAILED");
    expect(next.progress).toBe(61);
  });

  it("keeps a queued job queued", () => {
    const queued = job({ job_id: "queued", status: "AWAITING_UPLOAD" });

    expect(reduceStreamError(queued).job.status).toBe("AWAITING_UPLOAD");
  });

  it("releases the dead subscription so a refresh can re-subscribe", () => {
    expect(reduceStreamError(job({ status: "PROCESSING" })).resubscribe).toBe(true);
  });

  it("never attaches a failure message to a job that did not fail", () => {
    const running = job({ job_id: "running", status: "PROCESSING" });

    const { job: next } = reduceStreamError(running);

    expect(next.errorMessage).toBeUndefined();
  });
});

describe("jobCreatedAt", () => {
  it("prefers the client-side stamp when this browser set one", () => {
    expect(
      jobCreatedAt({ createdAt: "2026-09-21T10:00:00Z", created_at: "2026-09-21T09:00:00Z" }),
    ).toBe("2026-09-21T10:00:00Z");
  });

  it("falls back to the server's row timestamp", () => {
    // This is the case every row loaded from the history endpoint lands in: the
    // client-side field only exists for jobs started in this browser, which is
    // why the Created column used to read "—" for a restored history.
    expect(jobCreatedAt({ created_at: "2026-09-21T09:00:00Z" })).toBe("2026-09-21T09:00:00Z");
  });

  it("normalises a null server timestamp to undefined, not null", () => {
    // `formatDateTime`/`formatDateTimeOrNull` distinguish them, and a `null`
    // leaking through would read as "present but unparseable".
    expect(jobCreatedAt({ created_at: null })).toBeUndefined();
    expect(jobCreatedAt({})).toBeUndefined();
  });
});

describe("jobProgress", () => {
  it("passes through the progress the server reported", () => {
    expect(jobProgress({ status: "PROCESSING", progress: 42 })).toBe(42);
    expect(jobProgress({ status: "PROCESSING", progress: 0 })).toBe(0);
  });

  it("returns null when there is no real value, so nothing is announced", () => {
    // The pages used to render `progress ?? 45`, which `aria-valuenow` then
    // reported to assistive tech as a fact the backend never sent.
    expect(jobProgress({ status: "PROCESSING" })).toBeNull();
    expect(jobProgress({ status: "PENDING" })).toBeNull();
    expect(jobProgress({ status: "AWAITING_UPLOAD" })).toBeNull();
  });

  it("treats a completed job as genuinely 100%", () => {
    expect(jobProgress({ status: "COMPLETED" })).toBe(100);
  });

  it("ignores a non-finite value", () => {
    expect(jobProgress({ status: "PROCESSING", progress: Number.NaN })).toBeNull();
  });
});

/*
 * Whether a row renders the progress bar. It is not "can we compute a
 * percentage": the bar is dropped for a finished conversion to buy back the
 * width that lets the row fit on one line, and it is dropped on a phone for a
 * failure because a bar that will never move again is noise beside the status.
 */
describe("showsProgressBar", () => {
  const wide = { narrow: false };
  const phone = { narrow: true };

  it("never shows a bar for a completed conversion, at any width", () => {
    expect(showsProgressBar({ status: "COMPLETED" }, wide)).toBe(false);
    expect(showsProgressBar({ status: "COMPLETED" }, phone)).toBe(false);
  });

  it("shows a failed conversion's bar on a wide row only", () => {
    expect(showsProgressBar({ status: "FAILED" }, wide)).toBe(true);
    expect(showsProgressBar({ status: "FAILED" }, phone)).toBe(false);
  });

  it("always shows the bar while a conversion is still in flight", () => {
    for (const status of ["PENDING", "PROCESSING", "AWAITING_UPLOAD"]) {
      expect(showsProgressBar({ status }, wide)).toBe(true);
      expect(showsProgressBar({ status }, phone)).toBe(true);
    }
  });
});

describe("FINISHED_STATUSES", () => {
  it("is exactly the two terminal outcomes the Convert page can act on", () => {
    expect([...FINISHED_STATUSES].sort()).toEqual(["COMPLETED", "FAILED"]);
  });

  it("shares no member with the in-flight set", () => {
    for (const status of FINISHED_STATUSES) {
      expect(IN_FLIGHT_STATUSES.has(status)).toBe(false);
    }
  });
});

/*
 * The Convert page's inline queue is split into "Active" and "Recently
 * finished". The rules for the second half live in `recentFinishedJobs`, so they
 * are pinned here — the page is only presentation.
 */

describe("recentFinishedJobs", () => {
  const NOW = Date.parse("2026-09-22T12:00:00.000Z");
  const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();
  const ids = (list: UiJob[]) => list.map((j) => j.job_id);

  /** A conversion this session watched finish `finishedAgoMs` ago. */
  function completedJob(
    jobId: string,
    finishedAgoMs: number,
    overrides: Partial<UiJob> = {},
  ): UiJob {
    return job({
      job_id: jobId,
      status: "COMPLETED",
      finishedAt: iso(-finishedAgoMs),
      ...overrides,
    });
  }

  describe("the 30-minute window", () => {
    it("includes a job that finished inside the window", () => {
      const list = [completedJob("fresh", 5 * 60_000)];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual(["fresh"]);
    });

    it("excludes a job that finished exactly one window ago", () => {
      // The boundary is exclusive: `now - finishedAt < WINDOW`.
      const list = [completedJob("edge", RECENT_FINISHED_WINDOW_MS)];

      expect(recentFinishedJobs(list, { now: NOW })).toEqual([]);
    });

    it("includes a job that finished one millisecond inside the window", () => {
      const list = [completedJob("edge", RECENT_FINISHED_WINDOW_MS - 1)];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual(["edge"]);
    });

    it("counts a future timestamp as recent, not as stale", () => {
      // Clock skew between the browser and the server. Hiding a conversion the
      // user just watched finish is the worse error.
      const list = [completedJob("ahead", -2 * 60_000)];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual(["ahead"]);
    });
  });

  describe("status filtering", () => {
    it("never returns an in-flight job", () => {
      // These are Active's rows; listing them twice would be a lie about state.
      const list = [
        job({ job_id: "running", status: "PROCESSING", createdAt: iso(-1_000) }),
        job({ job_id: "queued", status: "PENDING", createdAt: iso(-1_000) }),
        job({ job_id: "uploading", status: "AWAITING_UPLOAD", createdAt: iso(-1_000) }),
      ];

      expect(recentFinishedJobs(list, { now: NOW })).toEqual([]);
    });

    it("includes a failed job, because the row carries its retry", () => {
      // A failure is actionable from here: the row shows the reason and a Retry,
      // so the user does not have to go to History to find out what happened.
      // `finishedAt` is the failure's own timestamp, not its start.
      const broke = job({
        job_id: "broke",
        status: "FAILED",
        createdAt: iso(-20 * 60_000),
        finishedAt: iso(-60_000),
        errorMessage: "libreoffice exploded",
      });

      expect(ids(recentFinishedJobs([broke], { now: NOW }))).toEqual(["broke"]);
    });

    it("places a failure by when it failed, not when it started", () => {
      // A 40-minute attempt that failed 1 minute ago is recent work; placed by
      // its start it would fall outside the window the moment it failed.
      const slowFailure = job({
        job_id: "slow-fail",
        status: "FAILED",
        createdAt: iso(-40 * 60_000),
        finishedAt: iso(-60_000),
      });
      // Same shape, but the session never saw the terminal event, so the only
      // timestamp available is the start — which is outside the window.
      const unknownFinish = job({
        job_id: "restored-fail",
        status: "FAILED",
        createdAt: iso(-40 * 60_000),
      });
      const list = [slowFailure, unknownFinish];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual(["slow-fail"]);
    });

    it("keeps every finished row of a mixed list and drops the running ones", () => {
      const list = [
        completedJob("done", 60_000),
        job({ job_id: "running", status: "PROCESSING", createdAt: iso(-1_000) }),
        job({
          job_id: "broke",
          status: "FAILED",
          finishedAt: iso(-2 * 60_000),
          errorMessage: "boom",
        }),
      ];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual(["done", "broke"]);
    });
  });

  describe("ordering and cap", () => {
    it("orders newest finish first", () => {
      const list = [
        completedJob("oldest", 10 * 60_000),
        completedJob("newest", 60_000),
        completedJob("middle", 5 * 60_000),
      ];

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual([
        "newest",
        "middle",
        "oldest",
      ]);
    });

    it("caps the list at five by default", () => {
      // Seven recent completions: only the five newest are listed inline.
      const list = [1, 2, 3, 4, 5, 6, 7].map((n) => completedJob(`j${n}`, n * 60_000));

      expect(ids(recentFinishedJobs(list, { now: NOW }))).toEqual([
        "j1",
        "j2",
        "j3",
        "j4",
        "j5",
      ]);
    });

    it("honours an explicit limit", () => {
      const list = [1, 2, 3, 4, 5].map((n) => completedJob(`j${n}`, n * 60_000));

      expect(ids(recentFinishedJobs(list, { now: NOW, limit: 2 }))).toEqual(["j1", "j2"]);
      expect(recentFinishedJobs(list, { now: NOW, limit: 0 })).toEqual([]);
    });

    it("does not mutate the list it reads", () => {
      const list = [completedJob("a", 60_000), completedJob("b", 120_000)];

      recentFinishedJobs(list, { now: NOW });

      expect(ids(list)).toEqual(["a", "b"]);
    });
  });

  describe("which timestamp places a job in time", () => {
    it("prefers when it finished over when it started", () => {
      // A slow conversion: started over an hour ago (outside the window on its
      // start time alone) but finished a minute ago. It is exactly what the
      // user is waiting to see.
      const slow = completedJob("slow", 60_000, { createdAt: iso(-60 * 60_000) });

      expect(ids(recentFinishedJobs([slow], { now: NOW }))).toEqual(["slow"]);
    });

    it("places a server-restored completion by its row timestamp", () => {
      // Rows loaded from the history endpoint carry only `created_at`; the
      // server has no completion timestamp for the SPA to read.
      const restored = job({
        job_id: "restored",
        status: "COMPLETED",
        created_at: iso(-2 * 60_000),
      });

      expect(ids(recentFinishedJobs([restored], { now: NOW }))).toEqual(["restored"]);
    });

    it("uses createdAt when the server row has no timestamp", () => {
      const local = job({
        job_id: "local",
        status: "COMPLETED",
        createdAt: iso(-2 * 60_000),
      });

      expect(ids(recentFinishedJobs([local], { now: NOW }))).toEqual(["local"]);
    });

    it("excludes a job with no timestamp at all", () => {
      // It cannot be placed in time, so calling it "recent" would be a guess.
      expect(recentFinishedJobs([job({ status: "COMPLETED" })], { now: NOW })).toEqual([]);
    });
  });

  describe("the Clear marker", () => {
    const clearedAt = iso(-60_000);
    const older = completedJob("older", 2 * 60_000);
    const exactlyAtMarker = completedJob("exactly", 60_000);
    const newer = completedJob("newer", 1_000);

    it("hides jobs that finished at or before the marker", () => {
      const list = [older, exactlyAtMarker, newer];

      expect(ids(recentFinishedJobs(list, { now: NOW, clearedBefore: clearedAt }))).toEqual([
        "newer",
      ]);
    });

    it("still shows a job that finishes after the clear", () => {
      // The whole reason the marker is a timestamp: a job finishing two seconds
      // later is newer than the marker and must reappear. A list of ids would
      // have to be extended on every future completion instead.
      const justFinished = completedJob("just-finished", -2_000);

      const result = recentFinishedJobs([older, exactlyAtMarker, justFinished], {
        now: NOW,
        clearedBefore: clearedAt,
      });

      expect(ids(result)).toEqual(["just-finished"]);
    });

    it("ignores an unparsable marker instead of clearing everything", () => {
      const list = [older, exactlyAtMarker, newer];

      expect(
        ids(recentFinishedJobs(list, { now: NOW, clearedBefore: "{not json" })),
      ).toEqual(["newer", "exactly", "older"]);
    });

    it("treats a missing marker as never cleared", () => {
      const list = [older, newer];

      expect(ids(recentFinishedJobs(list, { now: NOW, clearedBefore: null }))).toEqual([
        "newer",
        "older",
      ]);
    });
  });
});

describe("readQueueClearedAt / markQueueCleared", () => {
  const key = (userId: number | null) => storageKeyFor(userId, QUEUE_CLEARED_STORAGE_KEY);

  it("round-trips the marker for one identity", () => {
    const store = memoryStore();
    const at = "2026-09-22T12:00:00.000Z";

    markQueueCleared(7, at, store);

    expect(readQueueClearedAt(7, store)).toBe(at);
  });

  it("keeps each identity's marker to itself", () => {
    // Same reason the jobs cache is scoped: one account's clear must not hide
    // another account's completions on a shared browser.
    const store = memoryStore();
    markQueueCleared(1, "2026-09-22T12:00:00.000Z", store);

    expect(readQueueClearedAt(1, store)).toBe("2026-09-22T12:00:00.000Z");
    expect(readQueueClearedAt(2, store)).toBeNull();
    expect(readQueueClearedAt(null, store)).toBeNull();
  });

  it("gives anonymous sessions their own marker", () => {
    const store = memoryStore();
    markQueueCleared(null, "2026-09-22T12:00:00.000Z", store);

    expect(readQueueClearedAt(null, store)).toBe("2026-09-22T12:00:00.000Z");
    expect(readQueueClearedAt(7, store)).toBeNull();
  });

  it("writes to its own key, never into the jobs cache", () => {
    const store = memoryStore();
    markQueueCleared(7, "2026-09-22T12:00:00.000Z", store);

    expect(store.getItem(key(7))).toBe("2026-09-22T12:00:00.000Z");
    expect(store.getItem(storageKeyFor(7))).toBeNull();
    expect(JOBS_STORAGE_KEY).not.toBe(QUEUE_CLEARED_STORAGE_KEY);
  });

  it("honours a custom base key", () => {
    const store = memoryStore();

    markQueueCleared(7, "2026-09-22T12:00:00.000Z", store, "other_key");

    expect(readQueueClearedAt(7, store, "other_key")).toBe("2026-09-22T12:00:00.000Z");
    expect(readQueueClearedAt(7, store)).toBeNull();
  });

  it("reports 'never cleared' for missing, empty, unparsable or non-string data", () => {
    expect(readQueueClearedAt(7, memoryStore())).toBeNull();
    expect(readQueueClearedAt(7, memoryStore({ [key(7)]: "" }))).toBeNull();
    expect(readQueueClearedAt(7, memoryStore({ [key(7)]: "{not json" }))).toBeNull();
    expect(readQueueClearedAt(7, memoryStore({ [key(7)]: "null" }))).toBeNull();
    // A double that returns a non-string must not be trusted as a marker.
    const wrongType = {
      getItem: () => 42,
      setItem: () => undefined,
      removeItem: () => undefined,
    } as unknown as KeyValueStore;
    expect(readQueueClearedAt(7, wrongType)).toBeNull();
  });

  it("survives a storage backend that throws", () => {
    // Private-mode browsers can throw on access; a failed clear must not take
    // the page down, and must not look like a successful one either.
    const hostile = {
      getItem() {
        throw new Error("blocked");
      },
      setItem() {
        throw new Error("blocked");
      },
      removeItem() {},
    } as KeyValueStore;

    expect(() => markQueueCleared(7, "2026-09-22T12:00:00.000Z", hostile)).not.toThrow();
    expect(readQueueClearedAt(7, hostile)).toBeNull();
  });
});
