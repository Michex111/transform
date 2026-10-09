import { describe, expect, it } from "vitest";

import {
  normalizeApiLogDetail,
  normalizeApiLogList,
  normalizeApiMetrics,
  normalizeMcpActivityList,
  normalizeMcpConnections,
  normalizeMcpControl,
  normalizeMcpSummary,
} from "@/api/developerNormalize";

/**
 * These guard the same class of bug the rest of `normalize.ts` does: a `200`
 * whose body is missing a key, cast to the declared type and then dereferenced.
 * For these pages a missing `buckets` would throw on `.map` and a missing
 * `overview` would render `NaN%` in a summary card — a broken response that
 * looks like a real measurement.
 */

describe("normalizeApiMetrics", () => {
  it("degrades to an empty but renderable payload for an empty body", () => {
    const metrics = normalizeApiMetrics({});
    expect(metrics.buckets).toEqual([]);
    expect(metrics.values).toEqual([]);
    expect(metrics.overview.requests).toBe(0);
    expect(metrics.metric_label.length).toBeGreaterThan(0);
  });

  it("is lossless for a well-formed payload", () => {
    const payload = {
      range_key: "1h",
      start: "2026-10-09T11:00:00Z",
      end: "2026-10-09T12:00:00Z",
      bucket_seconds: 60,
      metric: "rate",
      metric_label: "Request rate",
      metric_unit: "req/s",
      generated_at: "2026-10-09T12:00:05Z",
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
      buckets: [
        { t: "2026-10-09T11:00:00Z", count: 10, success: 9, errors: 1, rate: 0.17, error_rate: 10, avg_latency_ms: 12.5, p50_ms: 10, p95_ms: 20, p99_ms: 30 },
      ],
      values: [0.17],
    };
    const metrics = normalizeApiMetrics(payload);
    expect(metrics.overview.requests).toBe(10);
    expect(metrics.buckets).toHaveLength(1);
    expect(metrics.values).toEqual([0.17]);
    expect(metrics.bucket_seconds).toBe(60);
  });

  it("keeps a null latency as null rather than zero", () => {
    // Coercing to 0 would draw a latency spike to the floor.
    const metrics = normalizeApiMetrics({
      buckets: [{ t: "x", count: 0, p95_ms: null }],
      values: [null],
    });
    expect(metrics.buckets[0].p95_ms).toBeNull();
    expect(metrics.values).toEqual([null]);
  });

  it("pads values to the bucket count when they disagree", () => {
    // A short `values` array would silently truncate the chart.
    const metrics = normalizeApiMetrics({
      buckets: [{ t: "a", count: 1 }, { t: "b", count: 2 }],
      values: [],
    });
    expect(metrics.values).toHaveLength(2);
    expect(metrics.values).toEqual([null, null]);
  });
});

describe("normalizeApiLogList", () => {
  it("yields an empty list for a non-array items field", () => {
    const page = normalizeApiLogList({ items: "nope" });
    expect(page.items).toEqual([]);
    expect(page.next_cursor).toBeNull();
  });

  it("derives the outcome from the status when it is absent", () => {
    const page = normalizeApiLogList({ items: [{ id: "a", status_code: 500 }] });
    expect(page.items[0].outcome).toBe("error");
    const ok = normalizeApiLogList({ items: [{ id: "b", status_code: 204 }] });
    expect(ok.items[0].outcome).toBe("success");
  });

  it("never turns a missing key name into a string", () => {
    const page = normalizeApiLogList({ items: [{ id: "a" }] });
    expect(page.items[0].api_key_name).toBeNull();
    expect(page.items[0].api_key_id).toBeNull();
  });
});

describe("normalizeApiLogDetail", () => {
  it("supplies a status sentence when the server omits one", () => {
    const detail = normalizeApiLogDetail({ entry: { id: "a", status_code: 200 } });
    expect(detail.status_meaning.length).toBeGreaterThan(0);
  });

  it("treats a request with no key as a session request", () => {
    const detail = normalizeApiLogDetail({ entry: { id: "a", api_key_id: null } });
    expect(detail.via_session).toBe(true);
  });
});

describe("normalizeMcpConnections", () => {
  it("falls back to REVOKED for an unrecognised status", () => {
    // Failing closed hides the "active" affordances instead of offering a
    // Resume for a connection in an unknown state.
    const list = normalizeMcpConnections({
      connections: [{ id: "c", status: "SOMETHING_NEW" }],
    });
    expect(list.connections[0].status).toBe("REVOKED");
  });

  it("keeps a recognised status", () => {
    const list = normalizeMcpConnections({ connections: [{ id: "c", status: "PAUSED" }] });
    expect(list.connections[0].status).toBe("PAUSED");
  });

  it("gives an unnamed application a readable fallback label", () => {
    const list = normalizeMcpConnections({ connections: [{ id: "c" }] });
    expect(list.connections[0].client_name).toBe("Unnamed application");
  });

  it("defaults the numeric counters so a card cannot render NaN", () => {
    const list = normalizeMcpConnections({ connections: [{ id: "c" }] });
    const connection = list.connections[0];
    expect(connection.requests).toBe(0);
    expect(connection.errors).toBe(0);
    expect(connection.denied).toBe(0);
  });
});

describe("normalizeMcpActivityList", () => {
  it("defaults an unknown outcome to ERROR rather than success", () => {
    const page = normalizeMcpActivityList({ items: [{ id: "i", outcome: "HMM" }] });
    expect(page.items[0].outcome).toBe("ERROR");
  });

  it("keeps DENIED distinct", () => {
    const page = normalizeMcpActivityList({ items: [{ id: "i", outcome: "DENIED" }] });
    expect(page.items[0].outcome).toBe("DENIED");
  });
});

describe("normalizeMcpSummary", () => {
  it("yields zeros and an empty tool list for an empty body", () => {
    const summary = normalizeMcpSummary({});
    expect(summary.connections).toBe(0);
    expect(summary.tools_used).toEqual([]);
  });
});

describe("normalizeMcpControl", () => {
  it("always carries a usable message", () => {
    const control = normalizeMcpControl({ connection: { id: "c", status: "PAUSED" } });
    expect(control.connection.status).toBe("PAUSED");
    expect(control.message.length).toBeGreaterThan(0);
  });
});
