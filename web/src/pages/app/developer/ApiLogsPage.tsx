import { useEffect, useMemo, useState } from "react";

import { api } from "@/api/client";
import type { ApiLogFilters } from "@/api/developerTypes";
import { ApiLogDetailsDrawer } from "@/components/developer/ApiLogDetailsDrawer";
import { ApiLogFilterBar } from "@/components/developer/ApiLogFilterBar";
import { ApiLogTable } from "@/components/developer/ApiLogTable";
import { ApiMetricsChart } from "@/components/developer/ApiMetricsChart";
import { DeveloperPageHeader } from "@/components/developer/DeveloperPageHeader";
import { MetricsModeToggle } from "@/components/developer/MetricsModeToggle";
import { Button, Card, Skeleton } from "@/components/ui";
import { Dropdown } from "@/components/Dropdown";
import { useApiLogs, useApiMetrics } from "@/lib/developerHooks";
import {
  METRIC_OPTIONS,
  formatCount,
  formatLatency,
  formatPercent,
  formatRate,
  metricOption,
} from "@/lib/developerMetrics";
import type { ApiMetric } from "@/api/developerTypes";

/**
 * Developer → API Logs.
 *
 * The page is one filter state driving two views: a summary + chart at the top
 * and the request explorer below. Both read the same `filters` object, so they
 * can never describe different traffic.
 *
 * Static/Live: the toggle only decides whether a stream is opened. In static
 * mode nothing polls — the snapshot is fetched on load and on an explicit
 * Refresh — which is the behaviour the mode promises and the reason it exists
 * (a dashboard that quietly polls is what users turn Live off to avoid).
 */
export function ApiLogsPage() {
  const [filters, setFilters] = useState<ApiLogFilters>({ range: "1h" });
  const [metric, setMetric] = useState<ApiMetric>("rate");
  const [live, setLive] = useState(false);
  const [apiKeyOptions, setApiKeyOptions] = useState<{ value: string; label: string }[]>([]);

  const { metrics, loading, error, liveState, refresh } = useApiMetrics(filters, live);
  const logs = useApiLogs(filters);

  // The key list only feeds a filter dropdown, so a failure here is silent: the
  // "All API keys" option still works and the log itself loads.
  useEffect(() => {
    let cancelled = false;
    void api
      .listApiKeys()
      .then((response) => {
        if (cancelled) return;
        setApiKeyOptions(
          (response.keys ?? []).map((key) => ({ value: key.id, label: key.name || key.id })),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const overview = metrics?.overview;
  const option = metricOption(metric);

  const cards = useMemo(
    () => [
      { label: "Total requests", value: overview ? formatCount(overview.requests) : "—" },
      {
        label: "Success rate",
        value: overview ? formatPercent(overview.success_rate) : "—",
        tone: overview && overview.success_rate < 99 ? ("warning" as const) : undefined,
      },
      {
        label: "Error rate",
        value: overview ? formatPercent(overview.error_rate) : "—",
        tone: overview && overview.error_rate > 1 ? ("error" as const) : undefined,
      },
      {
        label: "Current rate",
        value: overview ? `${formatRate(overview.current_rps)} req/s` : "—",
        hint: overview ? `${formatCount(overview.current_window_requests)} in the last minute` : undefined,
      },
      {
        label: "p95 latency",
        value: overview ? formatLatency(overview.p95_ms) : "—",
        hint: overview ? `p50 ${formatLatency(overview.p50_ms)} · p99 ${formatLatency(overview.p99_ms)}` : undefined,
      },
    ],
    [overview],
  );

  return (
    <div className="mx-auto max-w-6xl">
      <DeveloperPageHeader
        title="API Logs"
        description="Monitor requests made with your Transform API keys — traffic, latency, errors and the key behind each call."
        actions={
          <Button variant="secondary" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />

      <ApiLogFilterBar
        filters={filters}
        onChange={setFilters}
        onRefresh={refresh}
        refreshing={loading}
        apiKeyOptions={apiKeyOptions}
      />

      {/* Summary cards — compact, one row, so the chart keeps the space. */}
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {cards.map((card) => (
          <Card key={card.label} className="p-3">
            <p className="text-xs text-muted">{card.label}</p>
            {loading && !metrics ? (
              <Skeleton className="mt-2 h-7 w-16" />
            ) : (
              <p
                className={`mt-1 font-display text-xl font-semibold tabular-nums ${
                  card.tone === "error"
                    ? "text-error"
                    : card.tone === "warning"
                      ? "text-warning"
                      : "text-on-background"
                }`}
              >
                {card.value}
              </p>
            )}
            {card.hint && !loading ? (
              <p className="mt-0.5 truncate text-[11px] text-muted" title={card.hint}>
                {card.hint}
              </p>
            ) : null}
          </Card>
        ))}
      </div>

      {/* The chart, with its own controls. */}
      <Card className="mb-6 p-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Dropdown
            value={metric}
            onChange={(value) => setMetric(value as ApiMetric)}
            options={METRIC_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
            ariaLabel="Chart metric"
            label="Metric"
          />
          <span className="hidden flex-1 text-xs text-muted sm:block">{option.note}</span>
          <MetricsModeToggle live={live} onChange={setLive} />
        </div>
        <ApiMetricsChart
          metrics={metrics}
          metric={metric}
          loading={loading}
          error={error}
          onRetry={refresh}
          liveState={liveState}
        />
      </Card>

      {/* Request explorer. */}
      <section aria-labelledby="request-logs-heading">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="request-logs-heading" className="font-display text-lg font-semibold text-on-background">
            Request logs
          </h2>
          <span className="text-xs text-muted">
            {logs.loading ? "Loading…" : `${formatCount(logs.items.length)} shown`}
          </span>
        </div>

        {logs.error ? (
          <div role="alert" className="mb-3 rounded-lg border border-error/40 bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
            {logs.error}
          </div>
        ) : null}

        <ApiLogTable
          entries={logs.items}
          loading={logs.loading}
          onSelect={(entry) => void logs.openDetail(entry.id)}
        />

        {logs.hasMore ? (
          <div className="mt-3 flex justify-center">
            <Button variant="secondary" onClick={() => void logs.loadMore()} disabled={logs.loadingMore}>
              {logs.loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </section>

      <ApiLogDetailsDrawer
        open={logs.detail !== null || logs.detailLoading}
        loading={logs.detailLoading}
        detail={logs.detail}
        onClose={logs.closeDetail}
      />
    </div>
  );
}
