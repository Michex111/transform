// conversionMapStore — the single source of truth for the source → target
// conversion graph, cached in memory and persisted to localStorage.
//
// Why this exists
// ---------------
// The format pickers restrict what a user may choose with this graph. While the
// graph was still being fetched (or if the request failed) the pickers had no
// data to restrict against, so they offered the *entire* static format catalogue
// — including pairs the backend cannot convert, which then failed server-side.
// Every page also fetched the graph separately, so the first paint of each page
// hit that window again.
//
// Now the graph is fetched once per audience per session, persisted under a
// versioned localStorage entry, and hydrated synchronously before the first
// render. A returning visitor therefore has a correct picker immediately, and a
// background revalidation refreshes it without ever blanking the UI.
//
// Expiry: a persisted graph is trusted for at most `CONVERSION_MAP_MAX_AGE_MS`
// (one week). The graph only changes when the workers gain or lose a converter,
// but a cached edge list can also outlive a rollback, so serving one forever is
// not acceptable — an entry past its age is ignored and pruned, which makes the
// next load wait for a real fetch instead of picking from an unknown graph. The
// same limit applies to the in-memory copy, so a tab left open for weeks
// revalidates rather than pinning the graph it fetched when it was opened.
//
// The payload is the global conversion graph — identical for every visitor and
// tier — so persisting it is not a privacy concern. Guest and authed entries
// stay separate because they are served by different endpoints; if the audience
// being read has no entry of its own we borrow the other audience's snapshot
// (same graph) so a first signed-in visit is warm too.

import { api } from "@/api/client";

export type ConversionMap = Record<string, string[]>;

/** Which endpoint the graph was read from. */
export type ConversionMapAudience = "guest" | "authed";

export interface ConversionMapSnapshot {
  /** Source format → valid target formats. `{}` until something is known. */
  map: ConversionMap;
  /** True only while nothing is available to render and a fetch is expected. */
  loading: boolean;
  /** Failure message, or null. Never set while a usable map is cached. */
  error: string | null;
  /** True when the map came from storage and has not been confirmed yet. */
  stale: boolean;
  /** Epoch ms the map was last fetched from the API, or null when cached-only. */
  fetchedAt: number | null;
}

const STORAGE_KEYS: Record<ConversionMapAudience, string> = {
  guest: "transform_conversion_map:guest",
  authed: "transform_conversion_map:authed",
};

/** Bump when the persisted shape changes so old entries are discarded. */
const SCHEMA_VERSION = 1;

/**
 * How long a cached graph may be used before it is treated as unknown.
 *
 * One week: long enough that a returning visitor almost always gets an
 * instant, correct picker, short enough that a stale edge list can never
 * outlive the deployment that produced it by more than a release cycle.
 */
export const CONVERSION_MAP_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

const EMPTY_MAP: ConversionMap = Object.freeze({}) as ConversionMap;

/** Snapshot used during server rendering, where storage does not exist. */
const SERVER_SNAPSHOT: ConversionMapSnapshot = Object.freeze({
  map: EMPTY_MAP,
  loading: true,
  error: null,
  stale: false,
  fetchedAt: null,
});

interface StoredConversionMap {
  v: number;
  savedAt: string;
  conversions: ConversionMap;
}

interface AudienceState {
  snapshot: ConversionMapSnapshot;
  /** Whether the persisted payload has been read into `snapshot` yet. */
  hydrated: boolean;
  inFlight: Promise<void> | null;
  /** Set once a fetch succeeds, so we make at most one request per session. */
  fetched: boolean;
  listeners: Set<() => void>;
}

const states: Record<ConversionMapAudience, AudienceState> = {
  guest: initialState(),
  authed: initialState(),
};

function initialState(): AudienceState {
  return {
    snapshot: {
      map: EMPTY_MAP,
      loading: true,
      error: null,
      stale: false,
      fetchedAt: null,
    },
    hydrated: false,
    inFlight: null,
    fetched: false,
    listeners: new Set(),
  };
}

function otherAudience(audience: ConversionMapAudience): ConversionMapAudience {
  return audience === "guest" ? "authed" : "guest";
}

/**
 * Whether a `fetchedAt` instant is still within the cache's lifetime.
 *
 * An unknown timestamp never counts as fresh (nothing to age, so nothing to
 * trust), and a future one does not either — that means the clock moved
 * backwards, and trusting it would freeze the entry as permanently fresh.
 */
function isFresh(fetchedAt: number | null, now: number = Date.now()): boolean {
  if (fetchedAt === null || !Number.isFinite(fetchedAt)) return false;
  const age = now - fetchedAt;
  return age >= 0 && age < CONVERSION_MAP_MAX_AGE_MS;
}

/** localStorage, or null when it is unavailable (SSR, blocked, private mode). */
function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/**
 * Keep only well-formed edges. Storage can hold anything a previous version of
 * the app (or another script on the origin) wrote, and a malformed entry must
 * never reach a picker as a selectable format.
 */
function sanitizeConversionMap(value: unknown): ConversionMap {
  if (!value || typeof value !== "object" || Array.isArray(value)) return EMPTY_MAP;

  const clean: ConversionMap = {};
  let seen = false;
  for (const [source, targets] of Object.entries(value as Record<string, unknown>)) {
    if (!source || !Array.isArray(targets)) continue;
    const valid = targets.filter(
      (target): target is string => typeof target === "string" && target.length > 0,
    );
    if (!valid.length) continue;
    clean[source] = Array.from(new Set(valid));
    seen = true;
  }
  return seen ? clean : EMPTY_MAP;
}

function readStored(audience: ConversionMapAudience): { map: ConversionMap; fetchedAt: number | null } | null {
  const store = safeStorage();
  if (!store) return null;

  try {
    const raw = store.getItem(STORAGE_KEYS[audience]);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;

    const stored = parsed as Partial<StoredConversionMap>;
    if (stored.v !== SCHEMA_VERSION) return null;

    const map = sanitizeConversionMap(stored.conversions);
    if (!Object.keys(map).length) return null;

    const savedAt = typeof stored.savedAt === "string" ? Date.parse(stored.savedAt) : Number.NaN;
    const fetchedAt = Number.isFinite(savedAt) ? savedAt : null;

    // Expired: do not serve it. Pruning here (rather than at the next write)
    // keeps an unusable entry from lingering for the rest of the week, and
    // means a later read has nothing stale left to accidentally accept.
    if (!isFresh(fetchedAt)) {
      dropStored(audience);
      return null;
    }

    return { map, fetchedAt };
  } catch {
    return null;
  }
}

/** Remove a persisted entry (expired or unusable). */
function dropStored(audience: ConversionMapAudience): void {
  const store = safeStorage();
  if (!store) return;
  try {
    store.removeItem(STORAGE_KEYS[audience]);
  } catch {
    /* ignore */
  }
}

function persist(
  audience: ConversionMapAudience,
  map: ConversionMap,
  fetchedAt: number | null,
): void {
  const store = safeStorage();
  if (!store) return;

  const payload: StoredConversionMap = {
    v: SCHEMA_VERSION,
    savedAt: new Date(fetchedAt ?? Date.now()).toISOString(),
    conversions: map,
  };
  try {
    store.setItem(STORAGE_KEYS[audience], JSON.stringify(payload));
  } catch {
    /* quota exceeded or storage disabled — the in-memory map still works */
  }
}

function notify(audience: ConversionMapAudience): void {
  for (const listener of [...states[audience].listeners]) listener();
}

function snapshotsEqual(a: ConversionMapSnapshot, b: ConversionMapSnapshot): boolean {
  return (
    a.map === b.map &&
    a.loading === b.loading &&
    a.error === b.error &&
    a.stale === b.stale &&
    a.fetchedAt === b.fetchedAt
  );
}

/**
 * Replace the snapshot, but only when something actually changed.
 *
 * `useSyncExternalStore` re-renders whenever the snapshot identity changes, so a
 * no-op update must keep the previous object or consumers would loop.
 */
function updateSnapshot(
  audience: ConversionMapAudience,
  patch: Partial<ConversionMapSnapshot>,
): void {
  const state = states[audience];
  const next = { ...state.snapshot, ...patch };
  if (snapshotsEqual(next, state.snapshot)) return;
  state.snapshot = next;
  notify(audience);
}

/** Load the persisted map into memory (once per audience). Never notifies. */
function ensureHydrated(audience: ConversionMapAudience): void {
  const state = states[audience];
  if (state.hydrated) return;
  state.hydrated = true;

  // Own entry first; otherwise borrow the other audience's — both endpoints
  // serve the same global graph, so this is a valid warm start rather than a
  // stale read of someone else's data.
  const stored = readStored(audience) ?? readStored(otherAudience(audience));
  if (!stored) return;

  state.snapshot = {
    map: stored.map,
    loading: false,
    error: null,
    stale: true,
    fetchedAt: stored.fetchedAt,
  };
}

/**
 * The current snapshot for an audience.
 *
 * Safe to call during render: it performs the one-off storage read and returns
 * the (already updated) snapshot without notifying subscribers, so React sees a
 * stable value for that pass.
 */
export function getConversionMapSnapshot(
  audience: ConversionMapAudience,
): ConversionMapSnapshot {
  ensureHydrated(audience);
  return states[audience].snapshot;
}

/** Stable snapshot for server rendering, where localStorage does not exist. */
export function getServerConversionMapSnapshot(): ConversionMapSnapshot {
  return SERVER_SNAPSHOT;
}

/** Subscribe to snapshot changes for an audience. Returns an unsubscribe fn. */
export function subscribeConversionMap(
  audience: ConversionMapAudience,
  listener: () => void,
): () => void {
  const state = states[audience];
  state.listeners.add(listener);
  return () => {
    state.listeners.delete(listener);
  };
}

/**
 * Fetch the conversion graph, unless it was already fetched this session.
 *
 * At most one request per audience is in flight; concurrent callers share it. A
 * failure clears the in-flight entry so a later mount can retry, and it only
 * surfaces as `error` when there is no cached map to render from.
 *
 * "Already fetched this session" is bounded by `CONVERSION_MAP_MAX_AGE_MS`: a
 * tab left open longer than that revalidates on its next load request while
 * still showing the graph it has, so the UI never blanks out just because the
 * cache aged.
 *
 * @param force Re-read the graph even if a fresh copy is already loaded.
 */
export function loadConversionMap(
  audience: ConversionMapAudience,
  { force = false }: { force?: boolean } = {},
): Promise<void> {
  const state = states[audience];
  ensureHydrated(audience);

  if (state.fetched && !force && isFresh(state.snapshot.fetchedAt)) return Promise.resolve();
  if (state.inFlight) return state.inFlight;

  const request = (audience === "guest" ? api.guestConversionMap() : api.conversionMap())
    .then((res) => {
      const map = sanitizeConversionMap(res.conversions);
      const fetchedAt = Date.now();
      state.fetched = true;
      state.inFlight = null;

      // An empty graph is treated as "no data" rather than as a successful
      // result, so a cached map is never replaced by nothing and the pickers
      // keep working.
      if (!Object.keys(map).length) {
        updateSnapshot(audience, {
          loading: false,
          error: state.snapshot.error ?? "The server returned no supported conversions.",
        });
        return;
      }

      updateSnapshot(audience, { map, loading: false, error: null, stale: false, fetchedAt });
      persist(audience, map, fetchedAt);
    })
    .catch((err: unknown) => {
      state.inFlight = null;
      const message = err instanceof Error ? err.message : "Could not load the conversion map";
      updateSnapshot(audience, {
        // A cached graph still drives a correct picker, so a refresh failure
        // must not turn into an error state the UI would replace it with.
        error: Object.keys(state.snapshot.map).length ? null : message,
        loading: false,
      });
    });

  state.inFlight = request;
  return request;
}

/**
 * Forget the cached graphs (memory + storage).
 *
 * Used by tests and whenever the map must be re-read from scratch (e.g. after
 * an API origin change). Pass `clearStorage: false` to drop only the in-memory
 * copies.
 */
export function resetConversionMapCache({ clearStorage = true }: { clearStorage?: boolean } = {}): void {
  for (const audience of Object.keys(states) as ConversionMapAudience[]) {
    states[audience] = initialState();
    if (clearStorage) dropStored(audience);
  }
}

/** True when the snapshot for an audience came from storage only. */
export function isConversionMapStale(audience: ConversionMapAudience): boolean {
  return getConversionMapSnapshot(audience).stale;
}

/**
 * True when the loaded map is past its lifetime and the next load request will
 * re-read it. Exposed for diagnostics; the UI never needs to act on this.
 */
export function isConversionMapExpired(audience: ConversionMapAudience): boolean {
  const { fetchedAt } = getConversionMapSnapshot(audience);
  return fetchedAt !== null && !isFresh(fetchedAt);
}
