// useConversionMap — reads the real source → target conversion graph from the
// API and exposes it in the shapes the public format pages need.
//
// The graph is served from `conversionMapStore`, which keeps it in memory and in
// localStorage. Consequences worth knowing:
//
//   * The map is populated on the *first* render for a returning visitor, so
//     the format pickers are already restricted to real conversions and no page
//     has to flash a loading state on every visit.
//   * `loading` is true only when there is genuinely nothing to render yet. A
//     refresh of an existing map never blanks the UI.
//   * `error` is only reported when no usable map is available. A failed
//     refresh of a cached map stays silent — the cache is still correct.
//
// The hook never throws and never takes the caller's UI away on failure: callers
// render an honest empty state when `error` is set.

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import {
  getConversionMapSnapshot,
  getServerConversionMapSnapshot,
  loadConversionMap,
  subscribeConversionMap,
  type ConversionMapAudience,
} from "@/lib/conversionMapStore";
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
  /** True when the map was served from the local cache and is being revalidated. */
  stale: boolean;
}

/* ------------------------------------------------------------------ */
/* Hook                                                                */
/* ------------------------------------------------------------------ */

/**
 * @param guest Use the public (no-account) conversion map. Pass `true` on
 *   public marketing/format pages; leave `false` inside the authed app.
 * @param enabled Set to false to skip fetching (e.g. a modal that is closed).
 *   A cached map is still returned, so pickers inside it are correct on open.
 */
export function useConversionMap({
  guest = false,
  enabled = true,
}: { guest?: boolean; enabled?: boolean } = {}): ConversionMapResult {
  const audience: ConversionMapAudience = guest ? "guest" : "authed";

  const subscribe = useCallback(
    (listener: () => void) => subscribeConversionMap(audience, listener),
    [audience],
  );
  const getSnapshot = useCallback(() => getConversionMapSnapshot(audience), [audience]);

  // `getServerSnapshot` ignores storage, so server rendering stays deterministic.
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getServerConversionMapSnapshot);

  useEffect(() => {
    if (!enabled) return;
    // Resolves immediately (and re-renders nobody) when this session already
    // has the graph; otherwise it warms the cache for every other consumer.
    void loadConversionMap(audience);
  }, [audience, enabled]);

  const map = snapshot.map;
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
    () => ({
      map,
      sources,
      targets,
      supported,
      targetsFor,
      sourcesFor,
      loading: snapshot.loading,
      error: snapshot.error,
      stale: snapshot.stale,
    }),
    [
      map,
      sources,
      targets,
      supported,
      targetsFor,
      sourcesFor,
      snapshot.loading,
      snapshot.error,
      snapshot.stale,
    ],
  );
}
