import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ApiMetricsResponse } from "@/api/developerTypes";
import { ApiMetricsChart } from "@/components/developer/ApiMetricsChart";

/**
 * The chart is rendered through `renderToString` because the repo has no DOM
 * test environment. That is enough to pin the things that matter most here: the
 * four states are distinguishable, the graphic carries an accessible label, and
 * a nonvisual summary exists — none of which depend on layout.
 */

function bucket(overrides: Partial<ApiMetricsResponse["buckets"][number]> = {}) {
  return {
    t: "2026-10-09T11:00:00.000Z",
    count: 10,
    success: 9,
    errors: 1,
    rate: 0.17,
    error_rate: 10,
    avg_latency_ms: 12.5,
    p50_ms: 10,
    p95_ms: 20,
    p99_ms: 30,
    ...overrides,
  };
}

function metrics(overrides: Partial<ApiMetricsResponse> = {}): ApiMetricsResponse {
  return {
    range_key: "1h",
    start: "2026-10-09T11:00:00.000Z",
    end: "2026-10-09T12:00:00.000Z",
    bucket_seconds: 60,
    metric: "rate",
    metric_label: "Request rate",
    metric_unit: "req/s",
    generated_at: "2026-10-09T12:00:05.000Z",
    overview: {
      requests: 10,
      success: 9,
      errors: 1,
      success_rate: 90,
      error_rate: 10,
      avg_latency_ms: 12.5,
      p50_ms: 10,
      p95_ms: 20,
      p99_ms: 30,
      current_rps: 0.5,
      current_window_requests: 30,
    },
    buckets: [bucket(), bucket({ t: "2026-10-09T11:01:00.000Z", count: 0, errors: 0, rate: 0 })],
    values: [0.17, 0],
    ...overrides,
  };
}

function render(props: Partial<Parameters<typeof ApiMetricsChart>[0]> = {}) {
  return renderToString(
    <ApiMetricsChart
      metrics={null}
      metric="rate"
      loading={false}
      error={null}
      onRetry={vi.fn()}
      liveState="static"
      {...props}
    />,
  );
}

describe("ApiMetricsChart states", () => {
  it("shows a skeleton while the first snapshot is loading", () => {
    const html = render({ loading: true });
    expect(html).toContain("Loading metrics");
    expect(html).not.toContain("<svg");
  });

  it("shows an error with a retry affordance", () => {
    const html = render({ error: "Metrics could not be loaded." });
    expect(html).toContain("Metrics could not be loaded.");
    expect(html).toContain("Try again");
    // The alert role is what makes the failure announced, not just visible.
    expect(html).toContain('role="alert"');
  });

  it("shows an explicit empty state rather than a blank chart", () => {
    const html = render({
      metrics: metrics({ buckets: [], values: [], overview: { ...metrics().overview, requests: 0, errors: 0 } }),
    });
    expect(html).toContain("No requests in this period");
    // The graphic itself is not rendered — only the explanatory panel (which
    // does contain its own decorative icon).
    expect(html).not.toContain('role="img"');
  });
});

describe("ApiMetricsChart rendering", () => {
  it("renders an accessible graphic with the metric named", () => {
    const html = render({ metrics: metrics() });
    expect(html).toContain('role="img"');
    expect(html).toContain("Request rate");
    // The interval size and the freshness stamp are stated, so the numbers are
    // interpretable without hovering.
    expect(html).toContain("1m intervals");
    expect(html).toContain("Updated");
  });

  it("ships a nonvisual summary of the data", () => {
    const html = render({ metrics: metrics() });
    expect(html).toContain("sr-only");
    expect(html).toMatch(/requests in the period/);
  });

  it("carries a live region for the connection state, not the data", () => {
    const html = render({ metrics: metrics(), liveState: "live" });
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("Live");
  });

  it("reports a dropped stream as reconnecting, not as live", () => {
    // Telling a user their dashboard is live when the stream has dropped is the
    // failure this state exists to prevent.
    const html = render({ metrics: metrics(), liveState: "reconnecting" });
    expect(html).toContain("Reconnecting");
  });

  it("is keyboard reachable", () => {
    const html = render({ metrics: metrics() });
    expect(html).toContain('tabindex="0"');
  });

  it("writes the axis in the selected metric's units", () => {
    const html = render({ metrics: metrics(), metric: "p95" });
    expect(html).toContain("p95 latency");
    expect(html).toContain("ms");
  });
});
