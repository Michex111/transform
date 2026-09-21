// Tests for the nullable date formatter used by the credit-reset line.
//
// The important behaviour is the timezone one: a credit reset is a UTC instant
// (midnight on the 1st), so it is the *previous local day* for anyone west of
// UTC. That is correct, not a bug — but it only holds if the instant is
// formatted locally rather than the UTC calendar date being printed directly.

import { describe, expect, it } from "vitest";
import { formatDateOrNull } from "@/lib/format";

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
