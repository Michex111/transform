// Tests for the persistent conversion-map store.
//
// These are the guarantees the format pickers depend on: the graph must be
// available synchronously on the first read (so no picker ever offers a format
// the backend cannot convert), it must survive a reload, and a failed refresh
// must never take a usable cached graph away.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  conversionMap: vi.fn(),
  guestConversionMap: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  api: {
    conversionMap: mocks.conversionMap,
    guestConversionMap: mocks.guestConversionMap,
  },
}));

const GUEST_KEY = "transform_conversion_map:guest";
const AUTHED_KEY = "transform_conversion_map:authed";

const DAY = 24 * 60 * 60 * 1000;
const WEEK = 7 * DAY;

const GRAPH = { pdf: ["docx", "png"], docx: ["pdf"] };

/** Fresh store module (module state is per-module-instance). */
async function loadStore() {
  vi.resetModules();
  return await import("./conversionMapStore");
}

/** A minimal in-memory localStorage. */
function stubStorage(initial: Record<string, string> = {}) {
  const backing = new Map(Object.entries(initial));
  const storage = {
    getItem: (key: string) => backing.get(key) ?? null,
    setItem: (key: string, value: string) => void backing.set(key, value),
    removeItem: (key: string) => void backing.delete(key),
    clear: () => backing.clear(),
    key: (index: number) => Array.from(backing.keys())[index] ?? null,
    get length() {
      return backing.size;
    },
  };
  vi.stubGlobal("localStorage", storage);
  return { storage, backing };
}

/**
 * A stored payload, as this module writes it.
 *
 * `ageMs` is relative to now on purpose: the entry carries an expiry, so a
 * hardcoded timestamp would silently flip between "fresh" and "expired" as the
 * real clock advances past it.
 */
function storedPayload(
  conversions: unknown,
  { version = 1, ageMs = 60_000, savedAt }: { version?: number; ageMs?: number; savedAt?: string } = {},
) {
  const timestamp = savedAt ?? new Date(Date.now() - ageMs).toISOString();
  return JSON.stringify({ v: version, savedAt: timestamp, conversions });
}

beforeEach(() => {
  mocks.conversionMap.mockReset().mockResolvedValue({ conversions: GRAPH });
  mocks.guestConversionMap.mockReset().mockResolvedValue({ conversions: GRAPH });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("hydration from storage", () => {
  it("exposes the persisted graph on the very first read", async () => {
    const savedAt = new Date(Date.now() - 60_000).toISOString();
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { savedAt }) });
    const store = await loadStore();

    const snapshot = store.getConversionMapSnapshot("guest");

    expect(snapshot.map).toEqual(GRAPH);
    expect(snapshot.loading).toBe(false);
    expect(snapshot.error).toBeNull();
    // Cached, not yet confirmed by this session.
    expect(snapshot.stale).toBe(true);
    expect(snapshot.fetchedAt).toBe(Date.parse(savedAt));
  });

  it("borrows the other audience's graph when its own is missing", async () => {
    // A visitor who used the guest converter, then signed in: the signed-in
    // pages must not start from an empty map (both endpoints serve the same
    // global graph).
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("authed").map).toEqual(GRAPH);
  });

  it("does not borrow the other audience's graph when that one is expired", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { ageMs: WEEK + 1000 }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("authed").map).toEqual({});
    expect(store.getConversionMapSnapshot("authed").loading).toBe(true);
  });

  it("starts empty and loading when nothing is stored", async () => {
    stubStorage();
    const store = await loadStore();

    const snapshot = store.getConversionMapSnapshot("guest");
    expect(snapshot.map).toEqual({});
    expect(snapshot.loading).toBe(true);
    expect(snapshot.stale).toBe(false);
  });

  it("ignores a payload from an older schema version", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { version: 0 }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("guest").map).toEqual({});
  });

  it.each([
    ["not json at all", "}{"],
    ["an array", "[]"],
    ["a null payload", "null"],
    ["a payload without edges", storedPayload({})],
    ["non-array targets", storedPayload({ pdf: "docx" })],
    ["empty target lists", storedPayload({ pdf: [] })],
    ["non-string targets", storedPayload({ pdf: [1, 2] })],
  ])("ignores corrupt storage (%s)", async (_label, raw) => {
    stubStorage({ [GUEST_KEY]: raw });
    const store = await loadStore();

    const snapshot = store.getConversionMapSnapshot("guest");
    expect(snapshot.map).toEqual({});
    expect(snapshot.loading).toBe(true);
  });

  it("drops unusable edges but keeps the valid ones", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload({ pdf: ["docx", 7, ""], broken: null }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("guest").map).toEqual({ pdf: ["docx"] });
  });

  it("still works when storage is unavailable", async () => {
    vi.stubGlobal("localStorage", undefined);
    const store = await loadStore();

    await store.loadConversionMap("guest");

    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
  });
});

describe("expiry", () => {
  it("uses a one-week lifetime", async () => {
    const store = await loadStore();
    expect(store.CONVERSION_MAP_MAX_AGE_MS).toBe(WEEK);
  });

  it("hydrates an entry from just inside the week", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { ageMs: WEEK - 60_000 }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
    expect(store.isConversionMapExpired("guest")).toBe(false);
  });

  it("ignores and prunes an entry older than a week", async () => {
    const { backing } = stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { ageMs: WEEK + 1000 }) });
    const store = await loadStore();

    const snapshot = store.getConversionMapSnapshot("guest");
    // Nothing stale is served: the next load waits for a real fetch.
    expect(snapshot.map).toEqual({});
    expect(snapshot.loading).toBe(true);
    expect(snapshot.fetchedAt).toBeNull();
    expect(snapshot.stale).toBe(false);
    // The unusable entry is not left behind to be re-read.
    expect(backing.has(GUEST_KEY)).toBe(false);
  });

  it("refetches after an expired entry is dropped", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { ageMs: WEEK + 1000 }) });
    mocks.guestConversionMap.mockResolvedValue({ conversions: { pdf: ["docx"] } });
    const store = await loadStore();

    await store.loadConversionMap("guest");

    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(1);
    expect(store.getConversionMapSnapshot("guest").map).toEqual({ pdf: ["docx"] });
    expect(store.isConversionMapExpired("guest")).toBe(false);
  });

  it("ignores an entry stamped in the future", async () => {
    // A backwards clock change would otherwise freeze the entry as forever
    // fresh; it is treated as expired instead, so it is re-fetched.
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { ageMs: -DAY }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("guest").map).toEqual({});
    expect(store.getConversionMapSnapshot("guest").loading).toBe(true);
  });

  it("ignores an entry whose timestamp cannot be parsed", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH, { savedAt: "not a date" }) });
    const store = await loadStore();

    expect(store.getConversionMapSnapshot("guest").map).toEqual({});
  });

  it("revalidates the in-memory graph once it ages out", async () => {
    stubStorage();
    const store = await loadStore();
    await store.loadConversionMap("guest");
    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(1);

    // A tab left open past the lifetime must re-read the graph rather than pin
    // the one it fetched when it was opened.
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + WEEK + 1000);
    try {
      expect(store.isConversionMapExpired("guest")).toBe(true);

      await store.loadConversionMap("guest");

      expect(mocks.guestConversionMap).toHaveBeenCalledTimes(2);
      // The refetched graph is fresh again (its stamp is the shifted "now").
      expect(store.isConversionMapExpired("guest")).toBe(false);
      expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
    } finally {
      clock.mockRestore();
    }
  });

  it("keeps showing the aged graph when the revalidating fetch fails", async () => {
    stubStorage();
    const store = await loadStore();
    await store.loadConversionMap("guest");
    mocks.guestConversionMap.mockRejectedValue(new Error("offline"));

    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + WEEK + 1000);
    try {
      await store.loadConversionMap("guest");
    } finally {
      clock.mockRestore();
    }

    const snapshot = store.getConversionMapSnapshot("guest");
    // The picker keeps working; the expired graph is not blanked out.
    expect(snapshot.map).toEqual(GRAPH);
    expect(snapshot.error).toBeNull();
  });

  it("reports expiry only for a loaded graph", async () => {
    stubStorage();
    const store = await loadStore();

    expect(store.isConversionMapExpired("guest")).toBe(false);
  });

  it("stamps the persisted entry with the fetch time", async () => {
    const { backing } = stubStorage();
    const store = await loadStore();
    const before = Date.now();

    await store.loadConversionMap("guest");

    const stored = JSON.parse(backing.get(GUEST_KEY) ?? "{}");
    const savedAt = Date.parse(stored.savedAt);
    expect(savedAt).toBeGreaterThanOrEqual(before);
    expect(savedAt).toBeLessThanOrEqual(Date.now());
  });
});

describe("fetching", () => {
  it("persists a successful fetch and clears the stale flag", async () => {
    const { backing } = stubStorage();
    const store = await loadStore();

    await store.loadConversionMap("guest");

    const snapshot = store.getConversionMapSnapshot("guest");
    expect(snapshot.map).toEqual(GRAPH);
    expect(snapshot.stale).toBe(false);
    expect(snapshot.fetchedAt).toBeTypeOf("number");
    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(1);

    const persisted = JSON.parse(backing.get(GUEST_KEY) ?? "{}");
    expect(persisted.conversions).toEqual(GRAPH);
    expect(persisted.v).toBe(1);
  });

  it("makes one request per audience per session", async () => {
    stubStorage();
    const store = await loadStore();

    await Promise.all([
      store.loadConversionMap("guest"),
      store.loadConversionMap("guest"),
      store.loadConversionMap("guest"),
    ]);
    await store.loadConversionMap("guest");

    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(1);
  });

  it("keeps the two audiences independent", async () => {
    stubStorage();
    const store = await loadStore();

    await store.loadConversionMap("guest");
    await store.loadConversionMap("authed");

    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(1);
    expect(mocks.conversionMap).toHaveBeenCalledTimes(1);
  });

  it("persists each audience under its own key", async () => {
    const { backing } = stubStorage();
    mocks.conversionMap.mockResolvedValue({ conversions: { pdf: ["docx"] } });
    const store = await loadStore();

    await store.loadConversionMap("guest");
    await store.loadConversionMap("authed");

    expect(JSON.parse(backing.get(GUEST_KEY) ?? "{}").conversions).toEqual(GRAPH);
    expect(JSON.parse(backing.get(AUTHED_KEY) ?? "{}").conversions).toEqual({ pdf: ["docx"] });
  });

  it("re-reads the graph when forced", async () => {
    stubStorage();
    const store = await loadStore();

    await store.loadConversionMap("guest");
    await store.loadConversionMap("guest", { force: true });

    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(2);
  });

  it("keeps a cached graph when the refresh fails", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH) });
    mocks.guestConversionMap.mockRejectedValue(new Error("offline"));
    const store = await loadStore();

    await store.loadConversionMap("guest");

    const snapshot = store.getConversionMapSnapshot("guest");
    // The picker stays correct: no error is surfaced and the graph is intact.
    expect(snapshot.map).toEqual(GRAPH);
    expect(snapshot.error).toBeNull();
    expect(snapshot.loading).toBe(false);
    expect(snapshot.stale).toBe(true);
  });

  it("reports an error when a fetch fails with nothing cached", async () => {
    stubStorage();
    mocks.guestConversionMap.mockRejectedValue(new Error("offline"));
    const store = await loadStore();

    await store.loadConversionMap("guest");

    const snapshot = store.getConversionMapSnapshot("guest");
    expect(snapshot.error).toBe("offline");
    expect(snapshot.loading).toBe(false);
    expect(snapshot.map).toEqual({});
  });

  it("retries after a failure", async () => {
    stubStorage();
    mocks.guestConversionMap.mockRejectedValueOnce(new Error("offline"));
    const store = await loadStore();

    await store.loadConversionMap("guest");
    await store.loadConversionMap("guest");

    expect(mocks.guestConversionMap).toHaveBeenCalledTimes(2);
    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
    expect(store.getConversionMapSnapshot("guest").error).toBeNull();
  });

  it("does not replace a cached graph with an empty response", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH) });
    mocks.guestConversionMap.mockResolvedValue({ conversions: {} });
    const store = await loadStore();

    await store.loadConversionMap("guest");

    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
  });

  it("tolerates a storage write failure", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {},
    });
    const store = await loadStore();

    await store.loadConversionMap("guest");

    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
  });
});

describe("subscribers", () => {
  it("notifies on a real change only", async () => {
    stubStorage();
    const store = await loadStore();
    const listener = vi.fn();
    store.subscribeConversionMap("guest", listener);

    await store.loadConversionMap("guest");
    expect(listener).toHaveBeenCalledTimes(1);

    // Already fetched and unchanged: no further notification.
    await store.loadConversionMap("guest");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stops notifying after unsubscribing", async () => {
    stubStorage();
    const store = await loadStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribeConversionMap("guest", listener);

    unsubscribe();
    await store.loadConversionMap("guest");

    expect(listener).not.toHaveBeenCalled();
  });

  it("returns a referentially stable snapshot between changes", async () => {
    stubStorage();
    const store = await loadStore();

    const first = store.getConversionMapSnapshot("guest");
    expect(store.getConversionMapSnapshot("guest")).toBe(first);
  });
});

describe("reset", () => {
  it("clears memory and storage", async () => {
    const { backing } = stubStorage();
    const store = await loadStore();
    await store.loadConversionMap("guest");

    store.resetConversionMapCache();

    expect(backing.has(GUEST_KEY)).toBe(false);
    const snapshot = store.getConversionMapSnapshot("guest");
    expect(snapshot.map).toEqual({});
    expect(snapshot.loading).toBe(true);
  });

  it("can drop only the in-memory copy", async () => {
    const { backing } = stubStorage();
    const store = await loadStore();
    await store.loadConversionMap("guest");

    store.resetConversionMapCache({ clearStorage: false });

    // The payload survives, so the next read re-hydrates from it instead of
    // starting empty.
    expect(backing.has(GUEST_KEY)).toBe(true);
    expect(store.getConversionMapSnapshot("guest").map).toEqual(GRAPH);
    expect(store.getConversionMapSnapshot("guest").stale).toBe(true);
  });
});

describe("server snapshot", () => {
  it("ignores the cache so server rendering is deterministic", async () => {
    stubStorage({ [GUEST_KEY]: storedPayload(GRAPH) });
    const store = await loadStore();

    const server = store.getServerConversionMapSnapshot();

    expect(server.map).toEqual({});
    expect(server.loading).toBe(true);
    // Same object every call, as useSyncExternalStore requires.
    expect(store.getServerConversionMapSnapshot()).toBe(server);
  });
});
