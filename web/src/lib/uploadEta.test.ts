// Tests for the upload time-estimate and progress helpers.
//
// The ETA is the piece users read most closely during a long upload, and the
// failure modes are all *plausible-looking wrong numbers*: an estimate derived
// from two adjacent samples oscillates, a stalled transfer reads "0 seconds
// left", and a finished one reports a negative remainder. Each of those is
// pinned below.

import { describe, expect, it } from "vitest";
import {
  MAX_REMAINING_SECONDS,
  MIN_REMAINING_SECONDS,
  combineProgress,
  createEtaTracker,
  formatEta,
  formatProgressPercent,
  isUploadActive,
  isUploadRunning,
  isUploadTerminal,
  summarizeUploads,
  uploadBytesFromParts,
  type UploadStatus,
} from "@/lib/uploadEta";

/** One progress observation: cumulative bytes done, and its wall clock. */
type Sample = [number, number];

const MiB = 1024 * 1024;

/** Feed a tracker a byte/time sequence and return every non-null estimate. */
function estimates(total: number, samples: Sample[]): number[] {
  const tracker = createEtaTracker();
  const out: number[] = [];
  for (const [bytesDone, atMs] of samples) {
    tracker.sample(bytesDone, atMs);
    const remaining = tracker.remainingSeconds(total);
    if (remaining !== null) out.push(remaining);
  }
  return out;
}

/**
 * Every sample paired with the estimate that was on screen for it, so a test
 * can ask "what did the user see when the transfer was a quarter done?".
 */
function estimateRows(
  total: number,
  samples: readonly Sample[],
): Array<{ bytesDone: number; atMs: number; remaining: number | null }> {
  const tracker = createEtaTracker();
  return samples.map(([bytesDone, atMs]) => {
    tracker.sample(bytesDone, atMs);
    return { bytesDone, atMs, remaining: tracker.remainingSeconds(total) };
  });
}

/** Samples every 500 ms moving `bytesPerSample` each time. */
function steadySequence(bytesPerSample: number, count: number): Array<[number, number]> {
  const samples: Array<[number, number]> = [];
  for (let i = 0; i < count; i++) samples.push([bytesPerSample * i, i * 500]);
  return samples;
}

/**
 * The transfer the live browser test watched: 200 MiB, multipart, ~13.6 s.
 *
 * The shape is not a guess — it falls out of how the engine reports progress.
 * `runMultipart` PUTs three parts at once (`DEFAULT_MAX_PARALLEL_PARTS`) and
 * derives the whole-file figure from the *aggregate* of their counters, so this
 * is what the tracker actually receives:
 *
 *  - The three parts start together, so the browser buffers each one's opening
 *    chunk and fires all three `upload.onprogress` events within a few
 *    milliseconds of one another. Three 6 MiB deltas 5 ms apart is a
 *    1.2 GB/second "rate": a measurement of the socket buffer, off by two
 *    orders of magnitude from the connection.
 *  - Progress then settles into one aggregate event every ~415 ms, which is
 *    the cadence the tester described ("events roughly every few hundred ms").
 *
 * The old tracker's first trustworthy sample came from inside that opening
 * burst, so its first estimate was ~1 s; the running-minimum ratchet then held
 * the reading there for the rest of the transfer. This profile reproduces
 * exactly that: the pre-fix estimate is `1 second left` at 25%, 50% and 75%.
 */
function liveMultipartProfile(): { total: number; samples: Sample[]; durationMs: number } {
  const total = 200 * MiB;
  const burstAtMs = 400;
  const burstPerPart = 6 * MiB;
  const durationMs = 13_600;

  const samples: Sample[] = [[0, 0]];
  let bytesDone = 0;
  for (let part = 0; part < 3; part++) {
    bytesDone += burstPerPart;
    samples.push([bytesDone, burstAtMs + part * 5]);
  }

  const steadyFromMs = burstAtMs + 10;
  const steadyRate = (total - bytesDone) / ((durationMs - steadyFromMs) / 1000);
  for (let atMs = steadyFromMs + 415; atMs < durationMs; atMs += 415) {
    samples.push([
      Math.min(total, bytesDone + steadyRate * ((atMs - steadyFromMs) / 1000)),
      atMs,
    ]);
  }
  samples.push([total, durationMs]);
  return { total, samples, durationMs };
}

describe("createEtaTracker", () => {
  it("refuses to estimate from a single sample", () => {
    const tracker = createEtaTracker();
    tracker.sample(0, 0);
    expect(tracker.remainingSeconds(1_000)).toBeNull();
  });

  it("refuses to estimate from samples closer together than the minimum span", () => {
    // Two samples 400 ms apart measure a burst, not a connection.
    const tracker = createEtaTracker();
    tracker.sample(0, 0);
    tracker.sample(1_000_000, 400);
    expect(tracker.remainingSeconds(100_000_000)).toBeNull();
  });

  it("estimates once there is a real span of throughput", () => {
    // The span is past the ramp guard (MIN_ETA_SPAN_MS) on purpose: before that
    // the tracker deliberately answers "Calculating…", so a shorter sequence
    // would assert the opposite of the contract.
    const values = estimates(100, [
      [0, 0],
      [10, 2000],
      [20, 4000],
    ]);
    expect(values.length).toBeGreaterThan(0);
    expect(values[values.length - 1]).toBeGreaterThan(0);
    expect(Number.isFinite(values[values.length - 1])).toBe(true);
  });

  it("withholds a number while the connection is still ramping up", () => {
    // Measured live: during the first ~2 s of a 200 MB transfer the rate is
    // still climbing, and an estimate computed then was up to 5x pessimistic
    // ("about 1 min left" against 12 s truly remaining). An honest
    // "Calculating…" is the correct answer for that window.
    const tracker = createEtaTracker();
    tracker.sample(0, 0);
    tracker.sample(3_000_000, 1000);
    tracker.sample(7_000_000, 2000);

    expect(tracker.remainingSeconds(200 * MiB)).toBeNull();

    // Once the ramp is behind it, a number appears — and is a usable one.
    tracker.sample(12_000_000, 3200);
    const first = tracker.remainingSeconds(200 * MiB);
    expect(first).not.toBeNull();
    expect(first as number).toBeGreaterThan(1);
  });

  it("never reports zero or a negative remainder", () => {
    // The file finishes between the last two samples: the raw remainder is 0.
    const values = estimates(10, [
      [0, 0],
      [5, 1000],
      [10, 2000],
    ]);
    expect(values.every((value) => value >= 1)).toBe(true);
  });

  it("caps an absurd estimate when throughput is effectively zero", () => {
    const values = estimates(1_000_000_000_000_000, [
      [0, 0],
      [1, 1500],
      [1, 3000],
    ]);
    expect(Math.max(...values)).toBeLessThanOrEqual(MAX_REMAINING_SECONDS);
  });

  it("converges on the true remainder while throughput holds", () => {
    // A steady 10 KB per 500 ms over a 400 KB file: no burst, no ratchet, so
    // the reading has to simply track the work that is left. (The previous
    // version of this test asserted the estimate could never rise. That is the
    // contract the defect was built on — see the two tests below.)
    const total = 400_000;
    const rows = estimateRows(total, steadySequence(10_000, 20)).filter(
      (row) => row.remaining !== null,
    );
    expect(rows.length).toBeGreaterThan(5);
    const last = rows[rows.length - 1];
    const truthSeconds = (total - last.bytesDone) / 20_000;
    expect(last.remaining as number).toBeGreaterThanOrEqual(truthSeconds / 2);
    expect(last.remaining as number).toBeLessThanOrEqual(truthSeconds * 2);
    expect(rows.every((row) => (row.remaining as number) >= MIN_REMAINING_SECONDS)).toBe(true);
  });

  it("stays within 2x of the truth at 25%, 50% and 75% of a fast-start upload", () => {
    const { total, samples, durationMs } = liveMultipartProfile();
    const rows = estimateRows(total, samples);
    for (const fraction of [0.25, 0.5, 0.75]) {
      const row = rows.find((r) => r.bytesDone >= total * fraction && r.remaining !== null);
      expect(row, `no estimate at ${fraction * 100}%`).toBeDefined();
      const truthSeconds = (durationMs - row!.atMs) / 1000;
      expect(row!.remaining as number).toBeGreaterThanOrEqual(truthSeconds / 2);
      expect(row!.remaining as number).toBeLessThanOrEqual(truthSeconds * 2);
    }
  });

  it("never parks on the one-second floor while meaningful work remains", () => {
    const { total, samples } = liveMultipartProfile();
    const rows = estimateRows(total, samples).filter(
      (row) => row.remaining !== null && row.bytesDone < total * 0.95,
    );
    let longestFloorMs = 0;
    let floorStartedAt: number | null = null;
    for (const row of rows) {
      if (row.remaining === MIN_REMAINING_SECONDS) {
        floorStartedAt ??= row.atMs;
        longestFloorMs = Math.max(longestFloorMs, row.atMs - floorStartedAt);
      } else {
        floorStartedAt = null;
      }
    }
    // The live run read "1 second left" from 10% all the way to 100%.
    expect(longestFloorMs).toBeLessThan(3000);
  });

  it("raises the estimate again after the transfer slows down", () => {
    // This is the defect, stated directly: a fast start, a stall, then fast
    // again. An estimate that can only ever fall cannot describe the middle of
    // that transfer, and the user reads a confidently wrong number.
    //
    // The timeline starts past the ramp guard so the comparison is between real
    // estimates rather than against the "Calculating…" window.
    const total = 100 * MiB;
    const step = 5 * MiB;
    const samples: Sample[] = [];
    // Ramp-in (0..4000 ms), then fast (..5500), then stalled (..8000), then fast.
    for (let i = 1; i <= 4; i++) samples.push([i * step, 1000 + i * 500]);
    for (let i = 1; i <= 3; i++) samples.push([(4 + i) * step, 4000 + i * 500]);
    for (let i = 1; i <= 6; i++) samples.push([7 * step, 5500 + i * 500]); // nothing moves
    for (let i = 1; i <= 6; i++) samples.push([(7 + i) * step, 8500 + i * 500]);

    const rows = estimateRows(total, samples);
    const beforeStall = rows
      .filter((row) => row.atMs <= 5500)
      .map((row) => row.remaining)
      .filter((value): value is number => value !== null);
    const duringStall = rows
      .filter((row) => row.atMs > 5500 && row.atMs <= 8000)
      .map((row) => row.remaining ?? 0);

    expect(beforeStall.length).toBeGreaterThan(0);
    expect(Math.max(...duringStall)).toBeGreaterThan(Math.max(...beforeStall) * 1.5);
  });

  it("keeps a jittery transfer inside a sane band without wild swings", () => {
    // Alternating slow and fast intervals — the exact pattern that makes a raw
    // `remaining / instantaneousRate` flip between "40 s" and "3 min". The
    // reading is allowed to move in either direction (a transfer that really
    // slows down must say so), but it must not swing wildly sample to sample.
    const tracker = createEtaTracker();
    const values: number[] = [];
    let bytes = 0;
    for (let i = 0; i < 40; i++) {
      bytes += i % 2 === 0 ? 2_000_000 : 12_000_000;
      tracker.sample(bytes, i * 500);
      const remaining = tracker.remainingSeconds(500_000_000);
      if (remaining !== null) values.push(remaining);
    }
    expect(values.length).toBeGreaterThan(10);
    // Every reading is a plausible countdown...
    expect(Math.min(...values)).toBeGreaterThanOrEqual(1);
    expect(Math.max(...values)).toBeLessThanOrEqual(600);
    // ...no 500 ms step moves it by more than half, and the whole run stays
    // inside a 3x band rather than bouncing between two numbers.
    for (let i = 1; i < values.length; i++) {
      expect(Math.abs(values[i] - values[i - 1]) / values[i - 1]).toBeLessThanOrEqual(0.5);
    }
    expect(Math.max(...values) / Math.min(...values)).toBeLessThanOrEqual(3);
  });

  it("does not claim the transfer is finished when it stalls", () => {
    const values = estimates(100_000_000, [
      [0, 0],
      [20_000_000, 1000],
      [40_000_000, 2000],
      [40_000_000, 3000],
      [40_000_000, 4000],
      [40_000_000, 5000],
    ]);
    expect(values.length).toBeGreaterThan(0);
    expect(values.every((value) => value >= 1)).toBe(true);
    expect(values).not.toContain(0);
  });

  it("ignores a backwards sample left by a retried part", () => {
    const tracker = createEtaTracker();
    tracker.sample(50_000_000, 0);
    tracker.sample(60_000_000, 2000);
    // A part restarted, so the aggregate dipped.
    tracker.sample(45_000_000, 2500);
    tracker.sample(70_000_000, 3500);
    const remaining = tracker.remainingSeconds(100_000_000);
    expect(remaining).not.toBeNull();
    expect(remaining as number).toBeGreaterThanOrEqual(1);
  });

  it("returns null rather than a number when the rate is unusable", () => {
    const tracker = createEtaTracker();
    tracker.sample(0, 0);
    tracker.sample(0, 2000);
    // Zero bytes moved over a real span: no throughput, so no estimate.
    expect(tracker.remainingSeconds(1_000_000)).toBeNull();
  });
});

describe("formatEta", () => {
  it("shows a calculating state instead of a made-up number", () => {
    expect(formatEta(null)).toBe("Calculating…");
    expect(formatEta(undefined)).toBe("Calculating…");
    expect(formatEta(Number.NaN)).toBe("Calculating…");
  });

  it("reads seconds, minutes and hours the way a person would say them", () => {
    expect(formatEta(1)).toBe("1 second left");
    expect(formatEta(12)).toBe("12 seconds left");
    expect(formatEta(59)).toBe("59 seconds left");
    expect(formatEta(125)).toBe("about 2 min left");
    expect(formatEta(3905)).toBe("about 1 hr 5 min left");
    expect(formatEta(7200)).toBe("about 2 hrs left");
  });

  it("carries a rounded-up minute instead of printing a 60-minute hour", () => {
    expect(formatEta(3599)).toBe("about 1 hr left");
  });

  it("never prints below one second", () => {
    expect(formatEta(0)).toBe("1 second left");
    expect(formatEta(0.4)).toBe("1 second left");
  });
});

describe("formatProgressPercent", () => {
  it("returns a clamped whole number", () => {
    expect(formatProgressPercent(50, 100)).toBe(50);
    expect(formatProgressPercent(1, 3)).toBe(33);
    expect(formatProgressPercent(99.6, 100)).toBe(100);
  });

  it("clamps out-of-range input rather than rendering it", () => {
    expect(formatProgressPercent(-5, 100)).toBe(0);
    expect(formatProgressPercent(200, 100)).toBe(100);
    expect(formatProgressPercent(0.4, 100)).toBe(0);
  });

  it("returns null when there is no meaningful denominator", () => {
    expect(formatProgressPercent(10, 0)).toBeNull();
    expect(formatProgressPercent(10, -1)).toBeNull();
    expect(formatProgressPercent(10, Number.NaN)).toBeNull();
  });
});

describe("combineProgress", () => {
  it("weights by bytes, not by file count", () => {
    // A finished 100-byte file and an untouched 1 MB file. Averaging the two
    // percentages would claim 50% done.
    const overall = combineProgress([
      { bytesDone: 100, size: 100 },
      { bytesDone: 0, size: 1_000_000 },
    ]);
    expect(overall.bytesDone).toBe(100);
    expect(overall.bytesTotal).toBe(1_000_100);
    expect(overall.percent).toBe(0);
    expect(overall.percent).not.toBe(50);
  });

  it("is null for an empty set", () => {
    expect(combineProgress([])).toEqual({ bytesDone: 0, bytesTotal: 0, percent: null });
  });

  it("clamps a count that overshoots its own size", () => {
    const overall = combineProgress([{ bytesDone: 500, size: 100 }]);
    expect(overall.bytesDone).toBe(100);
    expect(overall.percent).toBe(100);
  });
});

describe("uploadBytesFromParts", () => {
  it("sums completed and in-flight bytes", () => {
    expect(uploadBytesFromParts({ completedBytes: 200, inFlightLoadedBytes: 50, size: 1000 })).toBe(
      250,
    );
  });

  it("clamps to the file size and to zero", () => {
    expect(uploadBytesFromParts({ completedBytes: 900, inFlightLoadedBytes: 900, size: 1000 })).toBe(
      1000,
    );
    expect(uploadBytesFromParts({ completedBytes: -50, inFlightLoadedBytes: 0, size: 1000 })).toBe(0);
    expect(
      uploadBytesFromParts({ completedBytes: Number.NaN, inFlightLoadedBytes: 0, size: 1000 }),
    ).toBe(0);
  });
});

describe("status helpers", () => {
  it("counts every not-yet-finished status as active", () => {
    expect(isUploadActive("queued")).toBe(true);
    expect(isUploadActive("uploading")).toBe(true);
    expect(isUploadActive("verifying")).toBe(true);
    expect(isUploadActive("done")).toBe(false);
    expect(isUploadActive("failed")).toBe(false);
    expect(isUploadActive("canceled")).toBe(false);
    expect(isUploadActive("interrupted")).toBe(false);
  });

  it("treats the outcome statuses as terminal, including the hydrated one", () => {
    expect(isUploadTerminal("interrupted")).toBe(true);
    expect(isUploadTerminal("done")).toBe(true);
    expect(isUploadTerminal("uploading")).toBe(false);
  });

  // The two predicates answer different questions and must not be collapsed:
  // a `queued` upload is in flight for the dock's heading but must never occupy
  // a concurrency slot, or the pump schedules nothing at all.
  it("counts a queued upload as in flight but not as running", () => {
    expect(isUploadActive("queued")).toBe(true);
    expect(isUploadRunning("queued")).toBe(false);
  });

  it("counts only the statuses that are moving or finalising bytes as running", () => {
    expect(isUploadRunning("uploading")).toBe(true);
    expect(isUploadRunning("verifying")).toBe(true);
    expect(isUploadRunning("done")).toBe(false);
    expect(isUploadRunning("failed")).toBe(false);
    expect(isUploadRunning("canceled")).toBe(false);
    expect(isUploadRunning("interrupted")).toBe(false);
  });
});

describe("summarizeUploads", () => {
  /** A list of rows carrying only the status the summary reads. */
  function withStatuses(...statuses: UploadStatus[]) {
    return statuses.map((status) => ({ status }));
  }

  it("calls a cancelled-only dock cancelled, never complete", () => {
    // The reported defect: the dock's only row read "Canceled" and the header
    // said "1 upload complete".
    const summary = summarizeUploads(withStatuses("canceled"));
    expect(summary.heading).toBe("1 upload canceled");
    expect(summary.heading.toLowerCase()).not.toContain("complete");
    expect(summary.canceled).toBe(1);
    expect(summary.done).toBe(0);
  });

  it("counts failed and canceled separately, and reports both", () => {
    const summary = summarizeUploads(withStatuses("failed", "canceled", "canceled"));
    expect(summary.failed).toBe(1);
    expect(summary.canceled).toBe(2);
    expect(summary.done).toBe(0);
    expect(summary.heading).toBe("1 upload failed · 2 uploads canceled");
    expect(summary.heading.toLowerCase()).not.toContain("complete");
  });

  it("counts a hydrated interruption as a failure, not a completion", () => {
    const summary = summarizeUploads(withStatuses("interrupted"));
    expect(summary.failed).toBe(1);
    expect(summary.done).toBe(0);
    expect(summary.heading).toBe("1 upload failed");
  });

  it("only says complete for uploads that actually completed", () => {
    const summary = summarizeUploads(withStatuses("done", "done", "failed"));
    expect(summary.done).toBe(2);
    expect(summary.failed).toBe(1);
    expect(summary.heading).toBe("2 uploads complete · 1 upload failed");
  });

  it("keeps the old single-status wording, singular and plural", () => {
    expect(summarizeUploads(withStatuses("done")).heading).toBe("1 upload complete");
    expect(summarizeUploads(withStatuses("done", "done")).heading).toBe("2 uploads complete");
    expect(summarizeUploads(withStatuses("done", "done", "done")).heading).toBe(
      "3 uploads complete",
    );
  });

  it("prioritises the running state while anything is still moving", () => {
    const summary = summarizeUploads(withStatuses("uploading", "done", "canceled"));
    expect(summary.active).toBe(1);
    expect(summary.heading).toBe("Uploading 1 file");
  });
});
