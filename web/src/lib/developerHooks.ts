import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/api/client";
import type {
  ApiLogDetailResponse,
  ApiLogEntry,
  ApiLogFilters,
  ApiMetricsResponse,
  McpActivityEntry,
  McpActivityFilters,
  McpActivitySummaryResponse,
  McpConnection,
} from "@/api/developerTypes";
import type { LiveState } from "@/components/developer/LiveIndicator";

/**
 * Data hooks for the Developer pages.
 *
 * Plain React state rather than a data-fetching library: the repo has no
 * TanStack Query, and introducing one for two pages would add a second caching
 * model beside the client's own GET cache and its identity-scoped stores. What
 * these hooks *do* own is the part that is easy to get wrong — the live stream's
 * lifecycle.
 *
 * The live stream is the sharp edge:
 *
 *  * exactly **one** subscription per (filters, live) pair, established in an
 *    effect and torn down in its cleanup, so a re-render cannot open a second
 *    stream (`subscribe` returns an abort function and the effect must call it);
 *  * a monotonic generation counter, because an aborted stream's error handler
 *    can still fire after cleanup — and a stale handler must not be able to
 *    overwrite the new stream's state or resurrect a dead connection;
 *  * bounded reconnect with backoff, so a dropped connection is retried a few
 *    times and then reported as Disconnected instead of spinning forever.
 *
 * Static mode never opens a stream, and never polls: the only way new data
 * arrives is an explicit refresh.
 */

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong. Please try again.";
}

/** A stable dependency key for a filter object (plain objects are not referentially stable). */
function filterKey(value: unknown): string {
  return JSON.stringify(value);
}

const MAX_LIVE_RETRIES = 5;

// ---------------------------------------------------------------------------
// API Logs — metrics
// ---------------------------------------------------------------------------

export function useApiMetrics(filters: ApiLogFilters, live: boolean) {
  const [metrics, setMetrics] = useState<ApiMetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [liveState, setLiveState] = useState<LiveState>("static");
  const [nonce, setNonce] = useState(0);

  const key = filterKey(filters);
  // The filters object is read inside effects that key on `key`; holding it in
  // a ref means the effect does not need `filters` in its dependency list
  // (which would restart the stream on every render).
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  // Snapshot: fetched on mount, on any filter change, and on every refresh.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .apiLogMetrics(filtersRef.current)
      .then((next) => {
        if (!cancelled) setMetrics(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, nonce]);

  // Live stream.
  useEffect(() => {
    if (!live) {
      setLiveState("static");
      return;
    }

    let cancelled = false;
    let stop: (() => void) | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    setLiveState("connecting");

    const connect = () => {
      if (cancelled) return;
      setLiveState(attempt === 0 ? "connecting" : "reconnecting");
      stop = api.subscribeApiMetrics(filtersRef.current, {
        onOpen: () => {
          if (cancelled) return;
          attempt = 0;
          setLiveState("live");
        },
        onMetrics: (next) => {
          if (cancelled) return;
          attempt = 0;
          setMetrics(next);
          setError(null);
          setLiveState("live");
        },
        onError: (message) => {
          if (cancelled) return;
          attempt += 1;
          setError(message);
          if (attempt > MAX_LIVE_RETRIES) {
            setLiveState("disconnected");
            return;
          }
          setLiveState("reconnecting");
          const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
          timer = setTimeout(connect, delay);
        },
      });
    };

    connect();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      // Closing the stream on teardown is what stops a connection leaking when
      // the user navigates away or switches Live off.
      stop?.();
      setLiveState("static");
    };
  }, [key, live]);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  return { metrics, loading, error, liveState, refresh };
}

// ---------------------------------------------------------------------------
// API Logs — the explorer
// ---------------------------------------------------------------------------

export function useApiLogs(filters: ApiLogFilters) {
  const [items, setItems] = useState<ApiLogEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<ApiLogDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const key = filterKey({ ...filters, cursor: undefined, limit: undefined });
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  // Guards against a superseded request writing over a newer page's results —
  // the same class of bug as `FilesPage.load`'s request-id guard.
  const requestId = useRef(0);

  useEffect(() => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    setItems([]);
    setCursor(null);
    void api
      .apiLogs(filtersRef.current)
      .then((page) => {
        if (id !== requestId.current) return;
        setItems(page.items);
        setCursor(page.next_cursor);
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (id !== requestId.current) return;
        setLoading(false);
      });
  }, [key]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    const id = requestId.current;
    setLoadingMore(true);
    try {
      const page = await api.apiLogs({ ...filtersRef.current, cursor });
      if (id !== requestId.current) return;
      // De-duplicate by id: a row can be re-served if a new request lands
      // between pages, and a duplicate React key would warn and mis-render.
      setItems((current) => {
        const seen = new Set(current.map((entry) => entry.id));
        return [...current, ...page.items.filter((entry) => !seen.has(entry.id))];
      });
      setCursor(page.next_cursor);
    } catch (err) {
      if (id === requestId.current) setError(errorMessage(err));
    } finally {
      if (id === requestId.current) setLoadingMore(false);
    }
  }, [cursor, loadingMore]);

  const openDetail = useCallback(async (eventId: string) => {
    setDetailLoading(true);
    try {
      setDetail(await api.apiLogDetail(eventId));
    } catch (err) {
      setError(errorMessage(err));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const closeDetail = useCallback(() => setDetail(null), []);

  return {
    items,
    loading,
    loadingMore,
    error,
    hasMore: Boolean(cursor),
    loadMore,
    detail,
    detailLoading,
    openDetail,
    closeDetail,
  };
}

// ---------------------------------------------------------------------------
// MCP Activity
// ---------------------------------------------------------------------------

export function useMcpConnections(range: ApiLogFilters["range"]) {
  const [connections, setConnections] = useState<McpConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .mcpConnections(range)
      .then((response) => {
        if (!cancelled) setConnections(response.connections);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, nonce]);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  /**
   * Replace one connection with the server's authoritative version.
   *
   * A mutation response is the single source of truth: patching the local list
   * from the request instead would let a failed call leave the UI claiming a
   * state the server never accepted.
   */
  const applyConnection = useCallback((connection: McpConnection) => {
    setConnections((current) =>
      current.map((entry) => (entry.id === connection.id ? connection : entry)),
    );
  }, []);

  return { connections, loading, error, refresh, applyConnection };
}

export function useMcpSummary(range: ApiLogFilters["range"]) {
  const [summary, setSummary] = useState<McpActivitySummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .mcpActivitySummary(range)
      .then((next) => {
        if (!cancelled) {
          setSummary(next);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range, nonce]);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);

  return { summary, loading, error, refresh };
}

export function useMcpActivity(filters: McpActivityFilters) {
  const [items, setItems] = useState<McpActivityEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const key = filterKey({ ...filters, cursor: undefined, limit: undefined });
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const requestId = useRef(0);

  useEffect(() => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    setItems([]);
    setCursor(null);
    void api
      .mcpActivity(filtersRef.current)
      .then((page) => {
        if (id !== requestId.current) return;
        setItems(page.items);
        setCursor(page.next_cursor);
      })
      .catch((err: unknown) => {
        if (id !== requestId.current) return;
        setError(errorMessage(err));
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false);
      });
  }, [key]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    const id = requestId.current;
    setLoadingMore(true);
    try {
      const page = await api.mcpActivity({ ...filtersRef.current, cursor });
      if (id !== requestId.current) return;
      setItems((current) => {
        const seen = new Set(current.map((entry) => entry.id));
        return [...current, ...page.items.filter((entry) => !seen.has(entry.id))];
      });
      setCursor(page.next_cursor);
    } catch (err) {
      if (id === requestId.current) setError(errorMessage(err));
    } finally {
      if (id === requestId.current) setLoadingMore(false);
    }
  }, [cursor, loadingMore]);

  return { items, loading, loadingMore, error, hasMore: Boolean(cursor), loadMore };
}

export type McpControlAction = "pause" | "resume" | "revoke";

/**
 * The pause/resume/revoke mutations.
 *
 * Each returns the server's authoritative connection so the caller can apply
 * it, and surfaces a per-connection pending flag so the correct row shows a
 * spinner — not all of them.
 */
export function useMcpControls() {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (
      action: McpControlAction,
      connectionId: string,
    ): Promise<{ connection: McpConnection; message: string } | null> => {
      setPendingId(connectionId);
      setError(null);
      try {
        const call =
          action === "pause"
            ? api.pauseMcpConnection
            : action === "resume"
              ? api.resumeMcpConnection
              : api.revokeMcpConnection;
        const result = await call(connectionId);
        return { connection: result.connection, message: result.message };
      } catch (err) {
        // Returning null (rather than throwing) keeps the call sites simple and
        // forces them to handle "the change did not happen" explicitly instead
        // of assuming success.
        setError(errorMessage(err));
        return null;
      } finally {
        setPendingId(null);
      }
    },
    [],
  );

  return useMemo(() => ({ pendingId, error, run }), [pendingId, error, run]);
}
