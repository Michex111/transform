// Tests for the bounded preview-bytes cache.
//
// The bounds are the point of this module, so they are what is pinned hardest:
// preview bytes are the largest thing the tab holds and `URL.createObjectURL`
// pins them, so an unbounded map here is a leak that only shows up on a long
// session. The other risk is key shape — a library file id and a job id are both
// UUIDs from different spaces, so a collision would serve one file's bytes for
// another.

import { describe, expect, it } from "vitest";
import { createPreviewCache, previewCacheKey, type CachedPreview } from "@/lib/previewCache";

/** A cached-preview value with a known byte size (no Blob API in a node env). */
function sized(bytes: number, fileName = "file.pdf"): CachedPreview {
  return { blob: { size: bytes } as Blob, fileName };
}

describe("previewCacheKey", () => {
  it("namespaces by source so a file and a job cannot collide", () => {
    expect(previewCacheKey("library", "id-1", "a.pdf")).toBe("library:id-1:a.pdf");
    expect(previewCacheKey("job", "id-1", "a.pdf")).toBe("job:id-1:a.pdf");
    expect(previewCacheKey("library", "id-1", "a.pdf")).not.toBe(
      previewCacheKey("job", "id-1", "a.pdf"),
    );
  });

  it("includes the name so a rename cannot answer for the old one", () => {
    // The name decides how the bytes are rendered, so an entry fetched under one
    // name must not be served for another.
    expect(previewCacheKey("library", "id-1", "a.jpg")).not.toBe(
      previewCacheKey("library", "id-1", "a.txt"),
    );
  });
});

describe("createPreviewCache — basics", () => {
  it("stores and retrieves an entry", () => {
    const cache = createPreviewCache();
    const entry = sized(10, "song.zip");

    cache.set("a", entry);

    expect(cache.get("a")).toBe(entry);
    expect(cache.get("a")?.fileName).toBe("song.zip");
    expect(cache.size()).toBe(1);
    expect(cache.bytes()).toBe(10);
  });

  it("returns undefined for a key it does not hold", () => {
    expect(createPreviewCache().get("missing")).toBeUndefined();
  });

  it("drops one entry without touching the others", () => {
    const cache = createPreviewCache();
    cache.set("a", sized(10));
    cache.set("b", sized(20));

    cache.drop("a");

    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
    expect(cache.bytes()).toBe(20);
  });

  it("clears everything", () => {
    const cache = createPreviewCache();
    cache.set("a", sized(10));
    cache.set("b", sized(20));

    cache.clear();

    expect(cache.size()).toBe(0);
    expect(cache.bytes()).toBe(0);
  });
});

describe("createPreviewCache — eviction", () => {
  it("evicts the least recently used entry past the entry cap", () => {
    const cache = createPreviewCache({ maxEntries: 2 });
    cache.set("a", sized(1));
    cache.set("b", sized(1));
    cache.set("c", sized(1));

    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
    expect(cache.get("c")).toBeDefined();
  });

  it("treats a read as a use, so the entry read last is evicted last", () => {
    const cache = createPreviewCache({ maxEntries: 2 });
    cache.set("a", sized(1));
    cache.set("b", sized(1));
    // Touch `a` so `b` becomes the least recently used one.
    expect(cache.get("a")).toBeDefined();

    cache.set("c", sized(1));

    expect(cache.get("a")).toBeDefined();
    expect(cache.get("b")).toBeUndefined();
  });

  it("evicts on the byte budget as well as the entry count", () => {
    const cache = createPreviewCache({ maxEntries: 10, maxBytes: 100 });
    cache.set("a", sized(60));
    cache.set("b", sized(60));

    // 120 > 100, so the older entry goes and the newest survives.
    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBeDefined();
  });

  it("keeps a single entry larger than the whole byte budget", () => {
    // The file the user just opened must still be cached even if it alone blows
    // the budget; refusing it would make the cache useless for big documents and
    // would mean re-downloading the largest files every time.
    const cache = createPreviewCache({ maxEntries: 10, maxBytes: 50 });
    const huge = sized(500);

    cache.set("huge", huge);

    expect(cache.get("huge")).toBe(huge);
    expect(cache.size()).toBe(1);
    expect(cache.bytes()).toBe(500);
  });

  it("still evicts down to one entry after a huge one is stored", () => {
    const cache = createPreviewCache({ maxEntries: 10, maxBytes: 100 });
    cache.set("small", sized(10));
    cache.set("huge", sized(500));

    // The huge entry is the most recent, so the small one is what goes.
    expect(cache.size()).toBe(1);
    expect(cache.get("huge")).toBeDefined();
    expect(cache.get("small")).toBeUndefined();
  });

  it("re-storing a key does not double-count its bytes", () => {
    const cache = createPreviewCache();
    cache.set("a", sized(10));
    cache.set("a", sized(10));

    expect(cache.size()).toBe(1);
    expect(cache.bytes()).toBe(10);
  });
});
