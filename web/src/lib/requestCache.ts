// A tiny read cache for the API client: it coalesces concurrent identical
// GETs and remembers a successful response for a short window.
//
// WHY IT EXISTS
// Measured against the running app: every SPA navigation refetched the same
// payloads. Returning to the Dashboard four times issued four identical
// `/user/dashboard` calls, and a single Dashboard load issued six — three
// separate callers (`DashboardPage`'s mount effect, its `credits:updated`
// listener, and `UploadsContext.refreshLimits`) each fetched independently,
// because there was no layer below them to share work.
//
// WHY NOT STALE-WHILE-REVALIDATE
// SWR hands back a cached value and refreshes behind it, so a page that never
// re-reads can sit on stale data indefinitely. A plain TTL bounds how old
// anything on screen can be, and because the only thing that really changes
// this data is a mutation, the cache is dropped outright whenever one succeeds
// (see `ApiClient.request`). That is simpler and strictly safer for values like
// a credit balance.
//
// WHY IT IS A PURE MODULE
// The repo has no jsdom, so anything living inside the client class would be
// untestable here. All the decisions — is this entry fresh, do two callers
// share one request, does a failure stick — are plain logic, so they live here
// where vitest can pin every branch.

/** Milliseconds since the epoch. Injected so tests control time. */
export type Clock = () => number;

export interface RequestCache {
  /**
   * Resolve `key` from the cache, or run `load` and remember the result.
   *
   * `ttlMs` of `0` means "coalesce only": concurrent callers share one in-flight
   * `load`, but a settled value is never reused. That is the right setting for
   * identity-critical reads (a stale token check would be a security problem,
   * not a stale-data problem).
   */
  run<T>(key: string, ttlMs: number, load: () => Promise<T>): Promise<T>;
  /** Drop one entry. */
  invalidate(key: string): void;
  /**
   * Drop every entry AND forget every in-flight request.
   *
   * Called after any successful mutation and on a token change, because both
   * mean "what we remembered may no longer be what the server would say".
   * Forgetting the in-flight promises matters as much: a request that started
   * before the mutation would otherwise settle into the cache afterwards and
   * re-seed it with pre-mutation data.
   */
  invalidateAll(): void;
  /** Entries currently held (diagnostics and tests). */
  size(): number;
}

interface Entry {
  value: unknown;
  storedAt: number;
}

/**
 * Create an independent cache.
 *
 * `inFlight` is intentionally not part of `Entry`: a request that has not
 * settled has no value and no age, and conflating the two is how a rejected
 * promise ends up being served to the next caller.
 */
export function createRequestCache(clock: Clock = Date.now): RequestCache {
  const entries = new Map<string, Entry>();
  const inFlight = new Map<string, Promise<unknown>>();
  // Bumped by `invalidateAll` so a request that began before the wipe cannot
  // write its (now possibly stale) result into the fresh cache when it settles.
  let generation = 0;

  return {
    run<T>(key: string, ttlMs: number, load: () => Promise<T>): Promise<T> {
      const shared = inFlight.get(key);
      if (shared) return shared as Promise<T>;

      if (ttlMs > 0) {
        const entry = entries.get(key);
        if (entry && clock() - entry.storedAt < ttlMs) {
          return Promise.resolve(entry.value as T);
        }
      }

      const startedAt = generation;
      const promise = load()
        .then((value) => {
          // Only remember a result that belongs to the current generation. A
          // mutation during the flight means this value predates it.
          //
          // `ttlMs > 0` as well: a 0 TTL means "coalesce only", so retaining a
          // value nothing will ever read would just hold memory for the life of
          // the tab (and make `size()` lie about what is cached).
          if (ttlMs > 0 && startedAt === generation) {
            entries.set(key, { value, storedAt: clock() });
          }
          return value;
        })
        .finally(() => {
          // Only clear the slot this call owns. If `invalidateAll` ran mid-flight
          // and a new call has already taken the key, deleting it here would
          // strand that newer request's dedupe.
          if (inFlight.get(key) === promise) inFlight.delete(key);
        });

      inFlight.set(key, promise);
      // A rejection must not be remembered: `entries` is only written on the
      // success path, so a transient failure is retried by the next caller
      // rather than served for the rest of the TTL.
      return promise;
    },

    invalidate(key: string): void {
      entries.delete(key);
    },

    invalidateAll(): void {
      generation += 1;
      entries.clear();
      inFlight.clear();
    },

    size: () => entries.size,
  };
}
