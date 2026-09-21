// useConversionMap — reads the real source → target conversion graph from the
// API and exposes it in the shapes the public format pages need.
//
// The graph is tiny (a few hundred edges) and every public page wants the same
// payload, so the in-flight/resolved promise is cached at module scope and
// shared: the landing page, the catalogue and every format hub resolve from a
// single request per audience. Guest and authed caches are kept separate
// because they hit different endpoints.
//
// The hook never throws. A network failure resolves to an empty map plus an
// `error` string so callers render an honest empty state instead of a grid of
// links to pages that cannot convert anything.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/api/client";
import { normalizeExt } from "@/lib/formatVisual";

export interface ConversionMapResult {
  /** Raw source → valid targets map. Empty object when unavailable. */
  map: Record<string, string[]>;
  /** Every format accepted as a source, in map order. */
  sources: string[];
  /** Every format that can be produced, de-duplicated. */
  targets: string[];
  /** Union of `sources` and `targets` — the formats that appear in the graph. */
  supported: Set<string>;
  /** Valid targets for a source; `[]` when the format cannot be a source. */
  targetsFor: (ext: string) => string[];
  /** Formats that can convert into `ext`; `[]` when nothing targets it. */
  sourcesFor: (ext: string) => string[];
  loading: boolean;
  /** Human-readable failure message, or null while loading/succeeded. */
  error: string | null;
}

/* ------------------------------------------------------------------ */
/* Module-level promise cache (one in-flight request per audience)      */
/* ------------------------------------------------------------------ */

let guestCache: Promise<Record<string, string[]>> | null = null;
let authedCache: Promise<Record<string, string[]>> | null = null;

function fetchConversionMap(guest: boolean): Promise<Record<string, string[]>> {
  const cached = guest ? guestCache : authedCache;
  if (cached) return cached;

  const request = (guest ? api.guestConversionMap() : api.conversionMap())
    .then((res) => res.conversions ?? {})
    .catch((err: unknown) => {
      // Drop the failed entry so a later mount can retry rather than being
      // pinned to an empty map for the lifetime of the tab.
      if (guest) guestCache = null;
      else authedCache = null;
      throw err instanceof Error ? err : new Error("Could not load the conversion map");
    });

  if (guest) guestCache = request;
  else authedCache = request;
  return request;
}

/** Test/seam helper: forget both cached maps (useful after signing in/out). */
export function resetConversionMapCache(): void {
  guestCache = null;
  authedCache = null;
}

/* ------------------------------------------------------------------ */
/* Hook                                                                */
/* ------------------------------------------------------------------ */

/**
 * @param guest Use the public (no-account) conversion map. Pass `true` on
 *   public marketing/format pages; leave `false` inside the authed app.
 */
export function useConversionMap({ guest = false }: { guest?: boolean } = {}): ConversionMapResult {
  const [map, setMap] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    fetchConversionMap(guest)
      .then((conversions) => {
        if (!mounted.current) return;
        setMap(conversions);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!mounted.current) return;
        setMap({});
        setError(err instanceof Error ? err.message : "Could not load the conversion map");
      })
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    return () => {
      mounted.current = false;
    };
  }, [guest]);

  const sources = useMemo(() => Object.keys(map), [map]);

  const targets = useMemo(() => {
    const unique = new Set<string>();
    for (const list of Object.values(map)) {
      for (const target of list) unique.add(target);
    }
    return Array.from(unique);
  }, [map]);

  // Reverse index, built once per map, so `sourcesFor` is O(1) per call rather
  // than scanning every edge on each render.
  const reverse = useMemo(() => {
    const index: Record<string, string[]> = {};
    for (const [source, list] of Object.entries(map)) {
      for (const target of list) {
        (index[target] ??= []).push(source);
      }
    }
    return index;
  }, [map]);

  const supported = useMemo(() => new Set([...sources, ...targets]), [sources, targets]);

  const targetsFor = useCallback((ext: string) => map[normalizeExt(ext)] ?? [], [map]);
  const sourcesFor = useCallback((ext: string) => reverse[normalizeExt(ext)] ?? [], [reverse]);

  return useMemo(
    () => ({ map, sources, targets, supported, targetsFor, sourcesFor, loading, error }),
    [map, sources, targets, supported, targetsFor, sourcesFor, loading, error],
  );
}
