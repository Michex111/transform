// Tests for the job-progress coercion and merge helpers.
//
// The regression these guard: an SSE frame that crossed Redis carries its
// numbers as strings, so `progress` arrived as `"25"`. Reading it as a number
// only made the bar render an indeterminate sweep instead of tracking the job.

import { describe, expect, it } from "vitest";
import { asNumeric, displayPercent, mergeProgress } from "@/lib/progress";

describe("asNumeric", () => {
  it("passes a finite number through", () => {
    expect(asNumeric(25)).toBe(25);
    expect(asNumeric(0)).toBe(0);
  });

  it("parses a numeric string, because Redis stringifies stream fields", () => {
    expect(asNumeric("25")).toBe(25);
    expect(asNumeric("0")).toBe(0);
    expect(asNumeric("" )).toBeNull();
    expect(asNumeric("  ")).toBeNull();
  });

  it("rejects non-numeric and non-finite values", () => {
    expect(asNumeric("downloading file")).toBeNull();
    expect(asNumeric(Number.NaN)).toBeNull();
    expect(asNumeric(Number.POSITIVE_INFINITY)).toBeNull();
    expect(asNumeric(null)).toBeNull();
    expect(asNumeric(undefined)).toBeNull();
    expect(asNumeric({})).toBeNull();
  });
});

describe("mergeProgress", () => {
  it("starts from an absent value", () => {
    expect(mergeProgress(undefined, "25")).toBe(25);
  });

  it("never moves backwards", () => {
    expect(mergeProgress(50, 25)).toBe(50);
    expect(mergeProgress(75, "50")).toBe(75);
  });

  it("moves forward", () => {
    expect(mergeProgress(25, 50)).toBe(50);
  });

  it("keeps the last real value when a frame carries none", () => {
    expect(mergeProgress(50, undefined)).toBe(50);
    expect(mergeProgress(50, "converting file")).toBe(50);
    expect(mergeProgress(undefined, undefined)).toBeUndefined();
  });

  it("settles a completed job at 100 regardless of the last frame", () => {
    expect(mergeProgress(75, undefined, { completed: true })).toBe(100);
    expect(mergeProgress(undefined, "0", { completed: true })).toBe(100);
  });
});

describe("displayPercent", () => {
  it("rounds and clamps to 0–100", () => {
    expect(displayPercent(42.4)).toBe(42);
    expect(displayPercent(120)).toBe(100);
    expect(displayPercent(-5)).toBe(0);
  });

  it("returns null for no value", () => {
    expect(displayPercent(null)).toBeNull();
  });
});
