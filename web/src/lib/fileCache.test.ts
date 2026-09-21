// Tests for the bounded retry cache.
//
// Every conversion used to pin its source `File` in memory for the whole
// session — only one retry branch ever released one — so a handful of large
// documents could exhaust the tab. These cases pin the bound *and* the rule that
// keeps History's retry working: a failed job's input is never released early.

import { describe, expect, it } from "vitest";
import { createFileCache, releaseCachedFile, type CachedUpload } from "@/lib/fileCache";

/** A minimal stand-in: the cache only ever reads `file.size`. */
function upload(size: number, label = "input"): CachedUpload {
  return {
    file: { name: `${label}.pdf`, size } as unknown as File,
    source: "pdf",
    target: "docx",
  };
}

describe("createFileCache bounds", () => {
  it("evicts the oldest entry once the entry limit is passed", () => {
    const cache = createFileCache({ maxFiles: 2 });

    cache.cacheFileForJob("a", upload(1, "a"));
    cache.cacheFileForJob("b", upload(1, "b"));
    cache.cacheFileForJob("c", upload(1, "c"));

    expect(cache.size()).toBe(2);
    expect(cache.getCachedFile("a")).toBeUndefined();
    expect(cache.getCachedFile("b")).toBeDefined();
    expect(cache.getCachedFile("c")).toBeDefined();
  });

  it("evicts by total bytes, dropping the oldest first", () => {
    const cache = createFileCache({ maxFiles: 10, maxBytes: 100 });

    cache.cacheFileForJob("a", upload(60, "a"));
    cache.cacheFileForJob("b", upload(60, "b"));

    expect(cache.bytes()).toBe(60);
    expect(cache.getCachedFile("a")).toBeUndefined();
    expect(cache.getCachedFile("b")).toBeDefined();
  });

  it("keeps a single oversized file rather than storing nothing", () => {
    const cache = createFileCache({ maxFiles: 4, maxBytes: 10 });

    // A 1 GB file is still the file the user is working with — dropping it
    // immediately would defeat the cache entirely.
    cache.cacheFileForJob("big", upload(1024));

    expect(cache.getCachedFile("big")).toBeDefined();
  });

  it("treats a re-cached or re-read entry as most recently used", () => {
    const cache = createFileCache({ maxFiles: 2 });

    cache.cacheFileForJob("a", upload(1, "a"));
    cache.cacheFileForJob("b", upload(1, "b"));
    // Touch "a" so "b" becomes the least recently used.
    expect(cache.getCachedFile("a")).toBeDefined();
    cache.cacheFileForJob("c", upload(1, "c"));

    expect(cache.getCachedFile("a")).toBeDefined();
    expect(cache.getCachedFile("b")).toBeUndefined();
  });

  it("drops and clears on demand", () => {
    const cache = createFileCache({ maxFiles: 4 });
    cache.cacheFileForJob("a", upload(1, "a"));
    cache.cacheFileForJob("b", upload(1, "b"));

    cache.dropCachedFile("a");
    expect(cache.getCachedFile("a")).toBeUndefined();
    expect(cache.getCachedFile("b")).toBeDefined();

    cache.clear();
    expect(cache.size()).toBe(0);
    expect(cache.bytes()).toBe(0);
  });
});

describe("releaseCachedFile (retry preservation)", () => {
  it("releases the input only after a successful conversion", () => {
    expect(releaseCachedFile("COMPLETED")).toBe(true);
  });

  it("never releases a failed job's input — History's retry re-uploads it", () => {
    // `HistoryPage.handleRetry` falls back to this cached file when the input
    // object is gone from storage, so evicting on FAILED would send the user
    // back to the converter to re-select a file we already hold.
    expect(releaseCachedFile("FAILED")).toBe(false);
  });

  it("leaves in-flight jobs alone", () => {
    for (const status of ["PENDING", "PROCESSING", "AWAITING_UPLOAD"]) {
      expect(releaseCachedFile(status)).toBe(false);
    }
  });

  it("keeps a failed job's file across unrelated insertions", () => {
    const cache = createFileCache({ maxFiles: 4 });
    cache.cacheFileForJob("failed", upload(1, "failed"));
    expect(releaseCachedFile("FAILED")).toBe(false);

    cache.cacheFileForJob("other-1", upload(1));
    cache.cacheFileForJob("other-2", upload(1));

    expect(cache.getCachedFile("failed")).toBeDefined();
  });
});
