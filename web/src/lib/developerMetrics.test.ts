import { describe, expect, it } from "vitest";

import {
  METRIC_OPTIONS,
  RANGE_OPTIONS,
  buildChartGeometry,
  connectionStatusMeta,
  formatBucketLabel,
  formatBucketSize,
  formatBytes,
  formatCount,
  formatFreshness,
  formatLatency,
  formatMetricValue,
  formatPercent,
  formatRate,
  logStatusMeta,
  metricOption,
  niceMax,
  toolOutcomeMeta,
} from "@/lib/developerMetrics";

describe("range and metric options", () => {
  it("offers the documented presets in order", () => {
    expect(RANGE_OPTIONS.map((option) => option.value)).toEqual([
      "5m",
      "15m",
      "1h",
      "24h",
      "7d",
      "30d",
    ]);
  });

  it("gives every metric a label and a unit", () => {
    // A chart axis with no unit is the failure mode this pins.
    for (const option of METRIC_OPTIONS) {
      expect(option.label.length).toBeGreaterThan(0);
      expect(option.unit.length).toBeGreaterThan(0);
    }
  });

  it("resolves an unknown metric to the default rather than undefined", () => {
    expect(metricOption("nonsense").value).toBe("rate");
  });
});

describe("number formatting", () => {
  it("groups large counts", () => {
    expect(formatCount(1284)).toBe("1,284");
  });

  it("scales rate precision with magnitude", () => {
    expect(formatRate(0.1234)).toBe("0.12");
    expect(formatRate(12.34)).toBe("12.3");
    expect(formatRate(1234.5)).toBe("1235");
  });

  it("switches latency to seconds past a second", () => {
    expect(formatLatency(184)).toBe("184 ms");
    expect(formatLatency(1500)).toBe("1.50 s");
  });

  it("renders an em dash for a missing measurement", () => {
    // Never "0"": the backend omits a value it did not measure, and printing a
    // zero would read as a real measurement.
    expect(formatLatency(null)).toBe("—");
    expect(formatRate(null)).toBe("—");
    expect(formatPercent(null)).toBe("—");
    expect(formatBytes(null)).toBeNull();
  });

  it("formats bytes with a unit and no false precision", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(1024 * 1024 * 3)).toBe("3.0 MB");
  });

  it("formats each metric with its own unit", () => {
    expect(formatMetricValue("rate", 2.5)).toBe("2.50");
    expect(formatMetricValue("count", 1200)).toBe("1,200");
    expect(formatMetricValue("error_rate", 0.6)).toBe("0.6%");
    expect(formatMetricValue("p95", 184)).toBe("184 ms");
    expect(formatMetricValue("p50", null)).toBe("—");
  });
});

describe("niceMax", () => {
  it("rounds an axis maximum up to a readable value", () => {
    expect(niceMax(3.7)).toBe(5);
    expect(niceMax(7.4)).toBe(10);
    expect(niceMax(120)).toBe(200);
  });

  it("never returns zero for empty or invalid input", () => {
    // A zero maximum would make every ratio in the geometry a division by zero.
    expect(niceMax(0)).toBe(1);
    expect(niceMax(Number.NaN)).toBe(1);
  });
});

describe("buildChartGeometry", () => {
  const options = { width: 720, height: 220, padding: { top: 10, right: 10, bottom: 20, left: 40 } };

  it("maps values across the full plot width", () => {
    const geometry = buildChartGeometry([0, 5, 10], options);
    expect(geometry.points).toHaveLength(3);
    expect(geometry.points[0].x).toBe(40);
    expect(geometry.points[2].x).toBe(710);
    // In SVG, y grows downward — the largest value must be nearest the top.
    expect(geometry.points[2].y).toBeLessThan(geometry.points[0].y);
  });

  it("breaks the line at a gap instead of bridging it", () => {
    // The core visual correctness rule: a null is missing data, and a single
    // continuous path would draw traffic that never happened.
    const geometry = buildChartGeometry([1, null, 3], options);
    expect(geometry.segments).toHaveLength(2);
    expect(geometry.points).toHaveLength(2);
    expect(geometry.areaPath).toBeNull();
  });

  it("fills an area only when the series is unbroken", () => {
    const geometry = buildChartGeometry([1, 2, 3], options);
    expect(geometry.segments).toHaveLength(1);
    expect(geometry.areaPath).toContain("Z");
  });

  it("centres a single point rather than pinning it to the axis", () => {
    const geometry = buildChartGeometry([4], options);
    expect(geometry.points).toHaveLength(1);
    expect(geometry.points[0].x).toBeGreaterThan(40);
    expect(geometry.points[0].x).toBeLessThan(720);
  });

  it("produces usable geometry for an all-null series", () => {
    const geometry = buildChartGeometry([null, null], options);
    expect(geometry.points).toEqual([]);
    expect(geometry.segments).toEqual([]);
    expect(geometry.yMax).toBeGreaterThan(0);
  });

  it("provides five evenly spaced y ticks", () => {
    const geometry = buildChartGeometry([0, 100], options);
    expect(geometry.yTicks).toHaveLength(5);
    expect(geometry.yTicks[0]).toBe(0);
    expect(geometry.yTicks[4]).toBe(geometry.yMax);
  });
});

describe("axis labels", () => {
  it("uses a precision that matches the bucket size", () => {
    const moment = "2026-10-09T10:07:33.000Z";
    // A one-second bucket is labelled to the second…
    expect(formatBucketLabel(moment, 1)).toMatch(/\d{2}:\d{2}:\d{2}/);
    // …an hourly bucket only to the minute, because it has no second precision
    // to report.
    const hourly = formatBucketLabel(moment, 3600);
    expect(hourly).toMatch(/\d{2}:\d{2}/);
    expect(hourly).not.toMatch(/\d{2}:\d{2}:\d{2}/);
  });

  it("describes the bucket size in words", () => {
    expect(formatBucketSize(15)).toBe("15s intervals");
    expect(formatBucketSize(60)).toBe("1m intervals");
    expect(formatBucketSize(3600)).toBe("1h intervals");
  });

  it("formats a freshness timestamp with seconds", () => {
    expect(formatFreshness("2026-10-09T10:42:08.000Z")).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(formatFreshness("not-a-date")).toBe("unknown");
  });
});

describe("status classification", () => {
  it("classifies an HTTP status without relying on colour alone", () => {
    expect(logStatusMeta(200)).toEqual({ tone: "success", label: "200" });
    expect(logStatusMeta(302).tone).toBe("neutral");
    expect(logStatusMeta(404).tone).toBe("error");
    // Rate limiting is a warning, not a failure — the client was throttled, the
    // server did what it was supposed to.
    expect(logStatusMeta(429).tone).toBe("warning");
    expect(logStatusMeta(503).tone).toBe("error");
  });

  it("distinguishes authorization status from connectivity", () => {
    expect(connectionStatusMeta("ACTIVE").tone).toBe("success");
    expect(connectionStatusMeta("PAUSED").tone).toBe("warning");
    expect(connectionStatusMeta("REVOKED").tone).toBe("error");
    // The paused copy must state that requests are refused, not that the agent
    // is disconnected.
    expect(connectionStatusMeta("PAUSED").description).toMatch(/refused/i);
  });

  it("gives a denied tool call its own wording", () => {
    // A blocked attempt is not a malfunction, and the copy says so.
    const denied = toolOutcomeMeta("DENIED");
    expect(denied.tone).toBe("warning");
    expect(denied.description).toMatch(/permission/i);
    expect(toolOutcomeMeta("ERROR").tone).toBe("error");
    expect(toolOutcomeMeta("SUCCESS").tone).toBe("success");
    // An unknown outcome is treated as a failure rather than silently as success.
    expect(toolOutcomeMeta("weird").tone).toBe("error");
  });
});
