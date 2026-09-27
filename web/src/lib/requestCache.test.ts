// Tests for the API read cache.
//
// Two behaviours carry all the risk here, so they get the most attention:
// a *rejected* request must never be remembered (or one network blip would
// break a screen for the whole TTL), and a mutation must not be able to leave
// pre-mutation data behind — including from a request that was already in
// flight when it happened.

import { describe, expect, it, vi } from "vitest";
import { createRequestCache } from "@/lib/requestCache";

/** A clock the test drives, so no test depends on wall time. */
function clockAt(start = 1_000) {
  let now = start;
  return { now: () => now, advance: (ms: number) => (now += ms) };
}

describe("createRequestCache — reuse", () => {
  it("runs the loader once and reuses the value inside the TTL", async () => {
    const clock = clockAt();
    const cache = createRequestCache(clock.now);
    const load = vi.fn(async () => "payload");

    expect(await cache.run("/thing", 10_000, load)).toBe("payload");
    clock.advance(9_999);
    expect(await cache.run("/thing", 10_000, load)).toBe("payload");

    expect(load).toHaveBeenCalledTimes(1);
  });

  it("reloads once the entry reaches the TTL boundary", async () => {
    const clock = clockAt();
    const cache = createRequestCache(clock.now);
    let n = 0;
    const load = vi.fn(async () => `payload-${++n}`);

    expect(await cache.run("/thing", 1_000, load)).toBe("payload-1");
    // Exactly at the TTL is already stale: the entry is only fresh while
    // `now - storedAt < ttlMs`, so the boundary cannot be ambiguous.
    clock.advance(1_000);
    expect(await cache.run("/thing", 1_000, load)).toBe("payload-2");

    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps different keys apart", async () => {
    const cache = createRequestCache();
    const load = vi.fn(async (v: string) => v);

    await cache.run("/a", 10_000, () => load("a"));
    await cache.run("/b", 10_000, () => load("b"));

    expect(cache.size()).toBe(2);
  });

  it("treats the query string as part of the key", async () => {
    // Callers build the query into the path, so `/items?page=1` and `?page=2`
    // are different entries and paging cannot serve page 2 from page 1's body.
    const cache = createRequestCache();
    const load = vi.fn(async () => "x");

    await cache.run("/items?page=1", 10_000, load);
    await cache.run("/items?page=2", 10_000, load);

    expect(load).toHaveBeenCalledTimes(2);
  });

  it("never reuses a settled value when the TTL is 0", async () => {
    const cache = createRequestCache();
    const load = vi.fn(async () => "payload");

    await cache.run("/me", 0, load);
    await cache.run("/me", 0, load);

    expect(load).toHaveBeenCalledTimes(2);
    expect(cache.size()).toBe(0);
  });
});

describe("createRequestCache — in-flight coalescing", () => {
  it("shares one request between concurrent callers", async () => {
    const cache = createRequestCache();
    let resolve!: (v: string) => void;
    const load = vi.fn(() => new Promise<string>((r) => (resolve = r)));

    const first = cache.run("/thing", 10_000, load);
    const second = cache.run("/thing", 10_000, load);
    resolve("payload");

    expect(await first).toBe("payload");
    expect(await second).toBe("payload");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("coalesces when the TTL is 0 too", async () => {
    // This is the whole point of a 0 TTL: three callers on one page load share
    // a single `/users/me` round trip.
    const cache = createRequestCache();
    let resolve!: (v: string) => void;
    const load = vi.fn(() => new Promise<string>((r) => (resolve = r)));

    const calls = [cache.run("/me", 0, load), cache.run("/me", 0, load), cache.run("/me", 0, load)];
    resolve("me");

    expect(await Promise.all(calls)).toEqual(["me", "me", "me"]);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("allows a fresh request once the in-flight one settled", async () => {
    const cache = createRequestCache();
    const load = vi.fn(async () => "payload");

    await cache.run("/thing", 0, load);
    await cache.run("/thing", 0, load);

    expect(load).toHaveBeenCalledTimes(2);
  });

  it("lets each caller see a rejection without caching it", async () => {
    const cache = createRequestCache();
    const load = vi.fn(async () => {
      throw new Error("boom");
    });

    const a = cache.run("/thing", 10_000, load);
    const b = cache.run("/thing", 10_000, load);

    await expect(a).rejects.toThrow("boom");
    await expect(b).rejects.toThrow("boom");
    expect(load).toHaveBeenCalledTimes(1);
    // Nothing was remembered, so the next caller is free to retry.
    expect(cache.size()).toBe(0);
  });

  it("retries after a failure instead of serving it for the TTL", async () => {
    const cache = createRequestCache();
    let attempt = 0;
    const load = vi.fn(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("transient");
      return "recovered";
    });

    await expect(cache.run("/thing", 10_000, load)).rejects.toThrow("transient");
    expect(await cache.run("/thing", 10_000, load)).toBe("recovered");
    expect(load).toHaveBeenCalledTimes(2);
  });
});

describe("createRequestCache — invalidation", () => {
  it("reloads after a single key is invalidated", async () => {
    const cache = createRequestCache();
    let n = 0;
    const load = vi.fn(async () => `v${++n}`);

    await cache.run("/a", 10_000, load);
    cache.invalidate("/a");
    expect(await cache.run("/a", 10_000, load)).toBe("v2");
  });

  it("drops every entry on invalidateAll", async () => {
    const cache = createRequestCache();
    const load = vi.fn(async () => "v");

    await cache.run("/a", 10_000, load);
    await cache.run("/b", 10_000, load);
    cache.invalidateAll();

    expect(cache.size()).toBe(0);
    await cache.run("/a", 10_000, load);
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("does NOT let an in-flight request re-seed the cache after a wipe", async () => {
    // The subtle one, and the reason `invalidateAll` bumps a generation: a
    // listing request started before a delete would otherwise settle *after*
    // the wipe and put the pre-delete listing straight back into the cache.
    const cache = createRequestCache();
    let resolve!: (v: string) => void;
    const load = vi.fn(() => new Promise<string>((r) => (resolve = r)));

    const inFlight = cache.run("/items", 10_000, load);
    cache.invalidateAll();
    resolve("pre-delete-listing");
    await inFlight;

    expect(cache.size()).toBe(0);
    // The next read must go back to the server.
    const second = vi.fn(async () => "post-delete-listing");
    expect(await cache.run("/items", 10_000, second)).toBe("post-delete-listing");
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("does not strand a newer in-flight request when an older one settles", async () => {
    // Two different requests for one key around a wipe must not delete each
    // other's coordination slot.
    const cache = createRequestCache();
    let resolveOld!: (v: string) => void;
    let resolveNew!: (v: string) => void;
    const oldLoad = vi.fn(() => new Promise<string>((r) => (resolveOld = r)));
    const newLoad = vi.fn(() => new Promise<string>((r) => (resolveNew = r)));

    const oldCall = cache.run("/thing", 10_000, oldLoad);
    cache.invalidateAll();
    const newCall = cache.run("/thing", 10_000, newLoad);
    const alsoNew = cache.run("/thing", 10_000, newLoad);

    resolveOld("old");
    await oldCall;
    resolveNew("new");

    expect(await newCall).toBe("new");
    expect(await alsoNew).toBe("new");
    expect(newLoad).toHaveBeenCalledTimes(1);
  });
});
