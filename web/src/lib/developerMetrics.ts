/**
 * Pure helpers for the Developer pages: metric metadata, number formatting and
 * SVG chart geometry.
 *
 * Deliberately dependency-free and DOM-free, for two reasons. The repo has no
 * charting library and adding one (Recharts + a shadcn chart wrapper) would pull
 * a whole rendering stack in for a single line chart — while a hand-built
 * `<svg>` gives exact control over the things that matter here (breaking the
 * line at a genuine gap, tabular axis ticks, an accessible summary). And keeping
 * the geometry as pure functions means it can be unit tested in the repo's
 * Node-only test environment, which is where every other rule-shaped module
 * lives (`formatRoutes`, `storageBreakdown`, `jobDetails`).
 */

import type { ApiMetric, ApiLogRange, McpConnectionStatus } from "@/api/developerTypes";

/** Window presets, in the order they are offered. */
export const RANGE_OPTIONS: { value: ApiLogRange; label: string; short: string }[] = [
  { value: "5m", label: "Last 5 minutes", short: "5m" },
  { value: "15m", label: "Last 15 minutes", short: "15m" },
  { value: "1h", label: "Last hour", short: "1h" },
  { value: "24h", label: "Last 24 hours", short: "24h" },
  { value: "7d", label: "Last 7 days", short: "7d" },
  { value: "30d", label: "Last 30 days", short: "30d" },
];

/**
 * The series the chart can plot.
 *
 * `note` is shown under the selector and states what the unit *means* — the
 * difference between "requests per second within this bucket" (an average) and
 * an instantaneous rate is exactly the kind of thing a chart must not leave
 * ambiguous.
 */
export const METRIC_OPTIONS: {
  value: ApiMetric;
  label: string;
  unit: string;
  note: string;
}[] = [
  {
    value: "rate",
    label: "Request rate",
    unit: "req/s",
    note: "Average requests per second within each interval.",
  },
  {
    value: "count",
    label: "Requests",
    unit: "requests",
    note: "Requests that completed within each interval.",
  },
  { value: "p50", label: "p50 latency", unit: "ms", note: "Median request duration." },
  { value: "p95", label: "p95 latency", unit: "ms", note: "95th percentile request duration." },
  {
    value: "error_rate",
    label: "Error rate",
    unit: "%",
    note: "Share of requests that returned a 4xx or 5xx status.",
  },
];

export function metricOption(metric: string) {
  return METRIC_OPTIONS.find((option) => option.value === metric) ?? METRIC_OPTIONS[0];
}

/** Compact integer formatting: 1_284 → "1,284". */
export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString("en-US");
}

/** Request rate with a precision that does not imply false accuracy. */
export function formatRate(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

/** Duration: sub-second in ms, a second or more in s. */
export function formatLatency(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms >= 100) return `${ms.toFixed(0)} ms`;
  return `${ms.toFixed(1)} ms`;
}

/** A percentage with a stable one-decimal precision. */
export function formatPercent(value: number | null, digits = 1): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

/** Byte counts, or `null` when the framework did not report one. */
export function formatBytes(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
}

/** The value formatting used by both the axis and the tooltip. */
export function formatMetricValue(metric: ApiMetric, value: number | null): string {
  switch (metric) {
    case "rate":
      return formatRate(value);
    case "count":
      return value === null ? "—" : formatCount(value);
    case "error_rate":
      return formatPercent(value);
    case "p50":
    case "p95":
      return formatLatency(value);
    default:
      return value === null ? "—" : String(value);
  }
}

/**
 * Round an axis maximum up to a readable value.
 *
 * A bare `max(values)` puts the peak exactly on the top gridline, where it is
 * clipped by the stroke width and impossible to read against the frame; and an
 * arbitrary max produces ticks like 3.7, 7.4. Rounding to 1/2/5 × 10ⁿ gives
 * both headroom and round numbers.
 */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const magnitude = Math.pow(10, exponent);
  const normalized = value / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

export interface ChartGeometry {
  width: number;
  height: number;
  /** Non-null points, in order, one per bucket that had data. */
  points: { x: number; y: number; index: number }[];
  /**
   * Polyline segments, already split wherever a value is `null`. The chart
   * draws one path per segment so a gap in the data is a gap in the line — a
   * single continuous path would imply traffic that never happened.
   */
  segments: { x: number; y: number }[][];
  /** The filled area under the (single-segment) line, or null when gapped. */
  areaPath: string | null;
  yMax: number;
  yTicks: number[];
  baselineY: number;
}

function buildLinePath(points: { x: number; y: number }[]): string {
  if (!points.length) return "";
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(" ");
}

export function buildChartGeometry(
  values: (number | null)[],
  {
    width = 720,
    height = 220,
    padding = { top: 12, right: 12, bottom: 24, left: 48 },
  }: {
    width?: number;
    height?: number;
    padding?: { top: number; right: number; bottom: number; left: number };
  } = {},
): ChartGeometry {
  const plotWidth = Math.max(1, width - padding.left - padding.right);
  const plotHeight = Math.max(1, height - padding.top - padding.bottom);
  const baselineY = padding.top + plotHeight;

  const numeric = values.filter((value): value is number => value !== null && Number.isFinite(value));
  const yMax = niceMax(numeric.length ? Math.max(...numeric) : 0);

  const count = values.length;
  // A single point is centred rather than pinned to the left edge, so a
  // one-bucket window (a 5-minute range at the coarsest bucket) draws a dot in
  // the middle of the plot instead of a stub against the axis.
  const step = count > 1 ? plotWidth / (count - 1) : 0;
  const xFor = (index: number) => (count > 1 ? padding.left + index * step : padding.left + plotWidth / 2);
  const yFor = (value: number) => baselineY - (value / yMax) * plotHeight;

  const points: { x: number; y: number; index: number }[] = [];
  const segments: { x: number; y: number }[][] = [];
  let current: { x: number; y: number }[] = [];

  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    const point = { x: xFor(index), y: yFor(value) };
    points.push({ ...point, index });
    current.push(point);
  });
  if (current.length) segments.push(current);

  let areaPath: string | null = null;
  if (segments.length === 1 && segments[0].length > 1) {
    const line = segments[0];
    const first = line[0];
    const last = line[line.length - 1];
    areaPath = `${buildLinePath(line)} L${last.x.toFixed(2)},${baselineY.toFixed(2)} L${first.x.toFixed(2)},${baselineY.toFixed(2)} Z`;
  }

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => fraction * yMax);

  return {
    width,
    height,
    points,
    segments,
    areaPath,
    yMax,
    yTicks,
    baselineY,
  };
}

/**
 * Format a bucket's x-axis label.
 *
 * The label depends on the bucket size, not the window: a one-second bucket
 * needs seconds, an hourly bucket needs the hour. Formatting every bucket as a
 * full timestamp would be unreadable at 168 points, and formatting an hourly
 * bucket as "10:00:00" implies a precision the bucket does not have.
 */
export function formatBucketLabel(iso: string, bucketSeconds: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  // Every branch names *all* the fields it shows. Leaving one out lets the
  // runtime's locale fill it in, which is how a seconds-level bucket ends up
  // labelled without seconds on one platform and with them on another.
  if (bucketSeconds < 60) {
    return date.toLocaleTimeString(undefined, {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }
  if (bucketSeconds < 3600) {
    return date.toLocaleTimeString(undefined, {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (bucketSeconds < 86400) {
    return date.toLocaleString(undefined, {
      hour12: false,
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** A human interval label for the chart caption, e.g. "15s intervals". */
export function formatBucketSize(seconds: number): string {
  if (seconds < 60) return `${seconds}s intervals`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m intervals`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h intervals`;
  return `${Math.round(seconds / 86400)}d intervals`;
}

/** Local time, with seconds, for the freshness indicator. */
export function formatFreshness(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Status classification for a log row, kept out of the component. */
export function logStatusMeta(statusCode: number): {
  tone: "success" | "warning" | "error" | "neutral";
  label: string;
} {
  const label = String(statusCode);
  if (statusCode >= 500) return { tone: "error", label };
  if (statusCode === 429) return { tone: "warning", label };
  if (statusCode >= 400) return { tone: "error", label };
  if (statusCode >= 300) return { tone: "neutral", label };
  return { tone: "success", label };
}

export function connectionStatusMeta(status: McpConnectionStatus): {
  label: string;
  tone: "success" | "warning" | "error" | "neutral";
  description: string;
} {
  switch (status) {
    case "ACTIVE":
      return {
        label: "Active",
        tone: "success",
        description: "Authorized. The agent can make requests.",
      };
    case "PAUSED":
      return {
        label: "Paused",
        tone: "warning",
        description: "Access suspended. The agent's requests are refused until you resume it.",
      };
    case "REVOKED":
      return {
        label: "Revoked",
        tone: "error",
        description: "Consent withdrawn. The agent must be authorized again to regain access.",
      };
    default:
      return { label: "Unknown", tone: "neutral", description: "Unrecognised authorization state." };
  }
}

export function toolOutcomeMeta(outcome: string): {
  label: string;
  tone: "success" | "warning" | "error";
  description: string;
} {
  switch (outcome) {
    case "SUCCESS":
      return { label: "Success", tone: "success", description: "The tool completed." };
    case "DENIED":
      return {
        label: "Denied",
        tone: "warning",
        description: "The tool was refused: the connection was not granted the required permission.",
      };
    default:
      return { label: "Failed", tone: "error", description: "The tool returned an error." };
  }
}
