// Tests for the nullable formatters used by the credit-reset line and the
// expanded job-details panel.
//
// Two behaviours matter here and both are about *absence*. A credit reset is a
// UTC instant (midnight on the 1st), so it is the *previous local day* for
// anyone west of UTC — correct, not a bug, but only if the instant is formatted
// locally rather than the UTC calendar date being printed directly. And the
// worker writes `compute_duration_ms = 0` for a job that has not run yet and
// for every job recorded before the field existed, so `formatDuration` must
// return `null` for it rather than a placeholder claiming the work took no
// time.

import { describe, expect, it } from "vitest";
import { formatDateTimeOrNull, formatDateOrNull, formatDuration } from "@/lib/format";

describe("formatDateOrNull", () => {
  it("returns null for absent or unparseable input", () => {
    expect(formatDateOrNull(null)).toBeNull();
    expect(formatDateOrNull(undefined)).toBeNull();
    expect(formatDateOrNull("")).toBeNull();
    expect(formatDateOrNull("not-a-date")).toBeNull();
  });

  it("renders a real instant with a month, day and year", () => {
    const out = formatDateOrNull("2026-10-01T00:00:00Z");
    expect(out).not.toBeNull();
    // Exact day depends on the test runner's timezone, so assert the shape and
    // that it names the right month/year.
    expect(out).toMatch(/^[A-Z][a-z]{2} \d{1,2}, 2026$/);
  });

  it("renders the local day for a UTC-midnight instant, not the UTC day", () => {
    // 2026-10-01T00:00Z is still 2026-09-30 in any timezone west of UTC. We
    // assert the invariant rather than a fixed string so the test is
    // timezone-independent: the formatted day is the instant's local day.
    const iso = "2026-10-01T00:00:00Z";
    const expectedDay = new Date(iso).getDate();
    const out = formatDateOrNull(iso);
    expect(out).toContain(String(expectedDay));
  });
});

describe("formatDateTimeOrNull", () => {
  it("returns null for absent or unparseable input", () => {
    expect(formatDateTimeOrNull(null)).toBeNull();
    expect(formatDateTimeOrNull(undefined)).toBeNull();
    expect(formatDateTimeOrNull("")).toBeNull();
    expect(formatDateTimeOrNull("not-a-date")).toBeNull();
  });

  it("renders the instant's local day and time", () => {
    const iso = "2026-09-21T13:45:00Z";
    const out = formatDateTimeOrNull(iso);
    expect(out).not.toBeNull();
    // The local hour depends on the runner's timezone, and the clock depends on
    // its locale (12-hour with an AM/PM suffix under the default en-US), so
    // assert the shape and the local day rather than a fixed string.
    expect(out).toMatch(/^[A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2}( ?[AP]M)?$/);
    expect(out).toContain(String(new Date(iso).getDate()));
  });
});

describe("formatDuration", () => {
  it("returns null when there is no reported duration", () => {
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
    expect(formatDuration(0)).toBeNull();
    expect(formatDuration(-5)).toBeNull();
    expect(formatDuration(Number.NaN)).toBeNull();
    expect(formatDuration(Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("keeps millisecond resolution below a second", () => {
    expect(formatDuration(1)).toBe("1 ms");
    expect(formatDuration(240)).toBe("240 ms");
    expect(formatDuration(999)).toBe("999 ms");
  });

  it("carries across the millisecond/second boundary rather than printing 1000 ms", () => {
    expect(formatDuration(999.6)).toBe("1.0 s");
    expect(formatDuration(1000)).toBe("1.0 s");
  });

  it("keeps one decimal below ten seconds, so short conversions stay distinguishable", () => {
    expect(formatDuration(1234)).toBe("1.2 s");
    expect(formatDuration(9500)).toBe("9.5 s");
    expect(formatDuration(9949)).toBe("9.9 s");
  });

  it("drops the decimal from ten seconds up", () => {
    expect(formatDuration(10000)).toBe("10 s");
    expect(formatDuration(12345)).toBe("12 s");
    expect(formatDuration(59000)).toBe("59 s");
  });

  it("rolls up to whole minutes without an empty seconds field", () => {
    expect(formatDuration(59500)).toBe("1 m");
    expect(formatDuration(60000)).toBe("1 m");
    expect(formatDuration(92000)).toBe("1 m 32 s");
    expect(formatDuration(120000)).toBe("2 m");
  });

  it("rounds a just-under-minute duration up rather than printing 0 m 60 s", () => {
    expect(formatDuration(59999)).toBe("1 m");
  });
});
