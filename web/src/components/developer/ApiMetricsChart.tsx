import { ChartLine } from "@phosphor-icons/react";
import { useMemo, useState } from "react";

import type { ApiMetric, ApiMetricsResponse } from "@/api/developerTypes";
import { LiveIndicator, type LiveState } from "@/components/developer/LiveIndicator";
import { Button, Skeleton } from "@/components/ui";
import {
  buildChartGeometry,
  formatBucketLabel,
  formatBucketSize,
  formatCount,
  formatFreshness,
  formatMetricValue,
  metricOption,
} from "@/lib/developerMetrics";
import { useElementWidth } from "@/lib/useElementWidth";

const PADDING = { top: 14, right: 14, bottom: 26, left: 56 };
const CHART_HEIGHT = 240;

/**
 * The API Logs time-series chart — the visual centrepiece of the page.
 *
 * Hand-built SVG rather than a charting library, for three reasons that all
 * matter here: the line must **break** at a bucket with no samples (a library
 * that interpolates across nulls would draw traffic that never happened), the
 * y-axis must format per metric (ms / req/s / %), and the whole thing has to be
 * keyboard navigable with a nonvisual summary. Those are all straightforward in
 * SVG and awkward through a generic chart API.
 *
 * Accessibility: the `<svg>` is focusable and arrow keys walk the buckets, with
 * the active bucket announced through a polite live region. A hidden text
 * summary (total, peak, window) is the equivalent nonvisual content a screen
 * reader gets instead of the graphic.
 */
export function ApiMetricsChart({
  metrics,
  metric,
  loading,
  error,
  onRetry,
  liveState,
}: {
  metrics: ApiMetricsResponse | null;
  metric: ApiMetric;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  liveState: LiveState;
}) {
  const [containerRef, width] = useElementWidth<HTMLDivElement>();
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const option = metricOption(metric);
  // Memoised so the downstream `useMemo`s depend on stable arrays: `metrics?.values
  // ?? []` allocates a fresh array on every render, which would invalidate the
  // geometry memo on every parent re-render (a live frame arrives every few
  // seconds, so "every render" is not rare here).
  const values = useMemo(() => metrics?.values ?? [], [metrics]);
  const buckets = useMemo(() => metrics?.buckets ?? [], [metrics]);

  const geometry = useMemo(
    () =>
      buildChartGeometry(values, {
        width: Math.max(320, width),
        height: CHART_HEIGHT,
        padding: PADDING,
      }),
    [values, width],
  );

  const summary = useMemo(() => {
    const numeric = values.filter((value): value is number => value !== null);
    // Totals come from the server's overview, the same source the summary cards
    // use — so the nonvisual description can never disagree with the cards
    // beside it. Peak and average are properties of the plotted series, which
    // only the buckets can give.
    const total = metrics?.overview.requests ?? 0;
    const errors = metrics?.overview.errors ?? 0;
    const peak = numeric.length ? Math.max(...numeric) : null;
    const average = numeric.length ? numeric.reduce((a, b) => a + b, 0) / numeric.length : null;
    return { total, errors, peak, average, points: numeric.length };
  }, [values, metrics]);

  const activeBucket = activeIndex !== null ? buckets[activeIndex] : null;
  const hasData = summary.total > 0 || values.some((value) => value !== null);

  // -- states --------------------------------------------------------------

  if (error) {
    return (
      <div
        role="alert"
        className="flex h-[240px] flex-col items-center justify-center gap-3 rounded-lg border border-error/40 bg-error-container/40 px-6 text-center"
      >
        <p className="text-sm font-medium text-on-error-container">Metrics could not be loaded.</p>
        <p className="max-w-md text-xs text-muted">{error}</p>
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }

  if (loading && !metrics) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading metrics">
        <Skeleton className="h-[240px] w-full" />
      </div>
    );
  }

  return (
    <figure className="m-0">
      <figcaption className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-2 text-sm font-medium text-on-background">
          <ChartLine size={16} className="text-primary" aria-hidden />
          {option.label}
          <span className="font-mono text-xs font-normal text-muted">({option.unit})</span>
        </span>
        {metrics ? (
          <span className="text-xs text-muted">
            {formatBucketSize(metrics.bucket_seconds)} · window{" "}
            {formatBucketLabel(metrics.start, 86400)}–{formatBucketLabel(metrics.end, 86400)}
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-2">
          {metrics ? (
            <span className="text-xs text-muted" title="When these aggregates were computed">
              Updated {formatFreshness(metrics.generated_at)}
            </span>
          ) : null}
          <LiveIndicator state={liveState} />
        </span>
      </figcaption>

      <div ref={containerRef} className="relative">
        {!hasData ? (
          <div className="flex h-[240px] flex-col items-center justify-center gap-2 rounded-lg border border-outline bg-surface-sunken text-center">
            <ChartLine size={26} className="text-muted" aria-hidden />
            <p className="text-sm font-medium text-on-background">No requests in this period</p>
            <p className="max-w-sm text-xs text-muted">
              Nothing was recorded for the selected window and filters. Try a longer time range, or
              clear a filter.
            </p>
          </div>
        ) : (
          <svg
            role="img"
            tabIndex={0}
            aria-label={`${option.label} chart. ${describeSummary(option.label, metric, summary)}`}
            width="100%"
            height={CHART_HEIGHT}
            viewBox={`0 0 ${Math.max(320, width)} ${CHART_HEIGHT}`}
            className="touch-none rounded-lg border border-outline bg-surface-sunken outline-none focus-visible:ring-2 focus-visible:ring-primary"
            onKeyDown={(event) => {
              if (!values.length) return;
              if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                event.preventDefault();
                setActiveIndex((current) => {
                  const start = current ?? (event.key === "ArrowRight" ? -1 : values.length);
                  const next = event.key === "ArrowRight" ? start + 1 : start - 1;
                  return Math.max(0, Math.min(values.length - 1, next));
                });
              } else if (event.key === "Home") {
                event.preventDefault();
                setActiveIndex(0);
              } else if (event.key === "End") {
                event.preventDefault();
                setActiveIndex(values.length - 1);
              } else if (event.key === "Escape") {
                setActiveIndex(null);
              }
            }}
            onBlur={() => setActiveIndex(null)}
          >
            <defs>
              <linearGradient id="api-log-area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-chart-1)" stopOpacity="0.32" />
                <stop offset="100%" stopColor="var(--color-chart-1)" stopOpacity="0.02" />
              </linearGradient>
            </defs>

            {/* Horizontal grid + y-axis ticks. */}
            {geometry.yTicks.map((tick, index) => {
              const y =
                geometry.baselineY -
                (tick / (geometry.yMax || 1)) * (geometry.baselineY - PADDING.top);
              return (
                <g key={`y-${index}`}>
                  <line
                    x1={PADDING.left}
                    x2={geometry.width - PADDING.right}
                    y1={y}
                    y2={y}
                    stroke="var(--color-outline)"
                    strokeWidth={1}
                    strokeDasharray={index === 0 ? undefined : "3 4"}
                  />
                  <text
                    x={PADDING.left - 8}
                    y={y + 4}
                    textAnchor="end"
                    className="fill-muted font-mono text-[10px] tabular-nums"
                  >
                    {formatMetricValue(metric, tick)}
                  </text>
                </g>
              );
            })}

            {/* X-axis labels: at most six, evenly spaced, so they never collide. */}
            {xTickIndices(values.length)
              .filter((index) => index < buckets.length)
              .map((index) => (
                <text
                  key={`x-${index}`}
                  x={geometry.points.find((point) => point.index === index)?.x ?? PADDING.left}
                  y={CHART_HEIGHT - 8}
                  textAnchor="middle"
                  className="fill-muted font-mono text-[10px]"
                >
                  {formatBucketLabel(buckets[index].t, metrics?.bucket_seconds ?? 60)}
                </text>
              ))}

            {/* The area is drawn only when the series has no gaps, because a
                filled shape spanning a gap would assert traffic that is not there. */}
            {geometry.areaPath ? (
              <path d={geometry.areaPath} fill="url(#api-log-area)" stroke="none" />
            ) : null}

            {/* One path per contiguous run: the line breaks at every gap. */}
            {geometry.segments.map((segment, index) =>
              segment.length === 1 ? (
                <circle
                  key={`pt-${index}`}
                  cx={segment[0].x}
                  cy={segment[0].y}
                  r={2.5}
                  fill="var(--color-chart-1)"
                />
              ) : (
                <path
                  key={`seg-${index}`}
                  d={segment
                    .map((point, i) => `${i === 0 ? "M" : "L"}${point.x},${point.y}`)
                    .join(" ")}
                  fill="none"
                  stroke="var(--color-chart-1)"
                  strokeWidth={1.75}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ),
            )}

            {/* Active-bucket marker. */}
            {activeIndex !== null && geometry.points.some((point) => point.index === activeIndex) ? (
              (() => {
                const point = geometry.points.find((p) => p.index === activeIndex);
                if (!point) return null;
                return (
                  <>
                    <line
                      x1={point.x}
                      x2={point.x}
                      y1={PADDING.top}
                      y2={geometry.baselineY}
                      stroke="var(--color-primary)"
                      strokeWidth={1}
                      strokeDasharray="3 3"
                    />
                    <circle cx={point.x} cy={point.y} r={4} fill="var(--color-primary)" />
                  </>
                );
              })()
            ) : null}

            {/* Invisible per-bucket hit areas. One rect per bucket rather than a
                single mousemove handler, so the hover target is exactly the
                bucket under the pointer and works for touch as well. */}
            {values.map((_, index) => {
              const x = geometry.points.find((point) => point.index === index)?.x;
              const slotWidth =
                values.length > 1
                  ? (geometry.width - PADDING.left - PADDING.right) / (values.length - 1)
                  : geometry.width - PADDING.left - PADDING.right;
              const left = (x ?? PADDING.left) - slotWidth / 2;
              return (
                <rect
                  key={`hit-${index}`}
                  x={Math.max(PADDING.left, left)}
                  y={PADDING.top}
                  width={Math.max(4, slotWidth)}
                  height={geometry.baselineY - PADDING.top}
                  fill="transparent"
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseLeave={() => setActiveIndex((current) => (current === index ? null : current))}
                />
              );
            })}
          </svg>
        )}

        {activeBucket && activeIndex !== null ? (
          <BucketTooltip
            bucket={activeBucket}
            metric={metric}
            unit={option.unit}
            x={geometry.points.find((point) => point.index === activeIndex)?.x ?? 0}
            containerWidth={Math.max(320, width)}
          />
        ) : null}
      </div>

      {/* Nonvisual equivalent: what the graphic shows, in words. */}
      <p className="sr-only">{describeSummary(option.label, metric, summary)}</p>
      <p className="sr-only" role="status" aria-live="polite">
        {activeBucket
          ? `${formatBucketLabel(activeBucket.t, metrics?.bucket_seconds ?? 60)}: ${formatMetricValue(
              metric,
              values[activeIndex ?? 0] ?? null,
            )}. ${formatCount(activeBucket.count)} requests, ${formatCount(activeBucket.errors)} errors.`
          : ""}
      </p>
    </figure>
  );
}

function describeSummary(
  label: string,
  metric: ApiMetric,
  summary: { total: number; errors: number; peak: number | null; average: number | null; points: number },
): string {
  if (!summary.points) return `${label} chart with no data points in this period.`;
  return `${summary.total} requests in the period, ${summary.errors} errors. ${label} peaks at ${formatMetricValue(
    metric,
    summary.peak,
  )} and averages ${formatMetricValue(metric, summary.average)} across ${summary.points} intervals.`;
}

/** At most six evenly spaced x labels. */
function xTickIndices(count: number): number[] {
  if (count <= 0) return [];
  const maxLabels = 6;
  const step = Math.max(1, Math.ceil(count / maxLabels));
  const indices: number[] = [];
  for (let index = 0; index < count; index += step) indices.push(index);
  if (indices[indices.length - 1] !== count - 1) indices.push(count - 1);
  return indices;
}

function BucketTooltip({
  bucket,
  metric,
  unit,
  x,
  containerWidth,
}: {
  bucket: ApiMetricsResponse["buckets"][number];
  metric: ApiMetric;
  unit: string;
  x: number;
  containerWidth: number;
}) {
  // Clamp the tooltip inside the container so a bucket at either edge does not
  // push the panel off-screen (a real defect at 390px in another component).
  const width = 200;
  const half = width / 2;
  const left = Math.min(Math.max(x, half + 4), containerWidth - half - 4);
  const primaryValue = metric === "count" ? bucket.count : metric === "error_rate" ? bucket.error_rate : metric === "p50" ? bucket.p50_ms : metric === "p95" ? bucket.p95_ms : bucket.rate;

  return (
    <div
      className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-outline bg-surface-raised p-2.5 shadow-[var(--shadow-e3)]"
      style={{ left, top: 8, width }}
      role="presentation"
    >
      <p className="mb-1.5 font-mono text-[11px] text-muted">
        {new Date(bucket.t).toLocaleString(undefined, { hour12: false })}
      </p>
      <dl className="space-y-0.5 text-xs">
        <TooltipRow label={metric === "p50" ? "p50" : metric === "p95" ? "p95" : "Value"}>
          <span className="font-mono tabular-nums text-on-background">
            {formatMetricValue(metric, primaryValue)} {unit}
          </span>
        </TooltipRow>
        <TooltipRow label="Requests">
          <span className="font-mono tabular-nums text-on-background">{formatCount(bucket.count)}</span>
        </TooltipRow>
        <TooltipRow label="Errors">
          <span className="font-mono tabular-nums text-on-background">{formatCount(bucket.errors)}</span>
        </TooltipRow>
        {bucket.p95_ms !== null ? (
          <TooltipRow label="p95">
            <span className="font-mono tabular-nums text-muted">{formatMetricValue("p95", bucket.p95_ms)}</span>
          </TooltipRow>
        ) : null}
      </dl>
      {bucket.count === 0 ? (
        <p className="mt-1.5 text-[11px] text-muted">No requests in this interval.</p>
      ) : null}
    </div>
  );
}

function TooltipRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
