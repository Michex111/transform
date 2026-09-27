// A bounded in-memory cache of preview bytes.
//
// WHY IT EXISTS
// `FilePreviewModal` used to download the whole file every time it opened.
// Measured against the running app: opening the same PDF three times issued
// three full `GET /files/{id}/download` requests, so a user flipping between two
// files re-downloaded both, in full, every time.
//
// WHY CACHING THESE BYTES IS ACCURATE
// A file's bytes are immutable under its id: re-uploading produces a new
// `user_files` row with a new id and a new object key, and the app never
// rewrites an existing object in place. So `id -> bytes` cannot go stale the way
// a listing or a credit balance can. The cache is still dropped wholesale on an
// identity change (a different account must not read another's bytes) and a
// single entry can be dropped explicitly.
//
// WHY IT IS BOUNDED
// Preview bytes are the largest things the tab holds — a 200 MB PDF is a 200 MB
// Blob, and `URL.createObjectURL` pins it. An unbounded map here would be a
// memory leak that only shows up on a long session, so the same LRU discipline
// (count AND bytes) the retry cache uses applies, with a much smaller budget:
// previews are transient glances, not work the user is mid-way through.

export interface PreviewCacheLimits {
  /** Most entries retained. */
  maxEntries: number;
  /**
   * Most bytes retained. A single entry larger than this is still kept (it is
   * the file the user just opened, so refusing it would make the cache useless
   * for the common case) and eviction then applies from the second entry.
   */
  maxBytes: number;
}

export const DEFAULT_PREVIEW_CACHE_LIMITS: PreviewCacheLimits = {
  maxEntries: 6,
  // 96 MB: several large documents stay warm, and a session cannot quietly pin
  // gigabytes of decoded page images.
  maxBytes: 96 * 1024 * 1024,
};

export interface PreviewCache {
  /** Remember bytes for a preview key, with the name they must be judged by. */
  set(key: string, entry: CachedPreview): void;
  /** Retrieve a preview key's bytes, if still held. */
  get(key: string): CachedPreview | undefined;
  /** Drop one entry (e.g. the file it belongs to was deleted). */
  drop(key: string): void;
  /** Drop everything (identity change / provider unmount). */
  clear(): void;
  /** Entries currently held. */
  size(): number;
  /** Total bytes currently held. */
  bytes(): number;
}

/**
 * What a cached preview holds.
 *
 * The name is stored with the bytes rather than taken from the caller on a hit,
 * because the name is what decides HOW the bytes are rendered. A row can name a
 * container after the target format (a multi-page `pdf -> jpg` emits a `.zip`),
 * so re-classifying cached `.zip` bytes from a stale row name would put a broken
 * image on screen — the exact bug the authoritative name was added to fix.
 */
export interface CachedPreview {
  blob: Blob;
  fileName: string;
}

/**
 * The cache key for a preview target.
 *
 * `kind` is part of the key because a library file id and a job id are both
 * UUIDs from different spaces — without it, a job whose id collided with a
 * file's would serve the wrong bytes.
 *
 * `fileName` is part of it so a rename invalidates itself: the name decides the
 * classification, so an entry fetched under one name must not answer for
 * another. (It is not a third namespace — it is the same target, described
 * differently.)
 */
export function previewCacheKey(kind: string, id: string, fileName: string): string {
  return `${kind}:${id}:${fileName}`;
}

/** Create an independent bounded preview cache. */
export function createPreviewCache(limits: Partial<PreviewCacheLimits> = {}): PreviewCache {
  const maxEntries = limits.maxEntries ?? DEFAULT_PREVIEW_CACHE_LIMITS.maxEntries;
  const maxBytes = limits.maxBytes ?? DEFAULT_PREVIEW_CACHE_LIMITS.maxBytes;

  // Insertion order is LRU order (reads re-insert), as in `fileCache`.
  const entries = new Map<string, CachedPreview>();

  function bytes(): number {
    let total = 0;
    for (const entry of entries.values()) total += entry.blob.size;
    return total;
  }

  function trim(): void {
    while (entries.size > maxEntries) {
      const oldest = entries.keys().next().value;
      if (oldest === undefined) return;
      entries.delete(oldest);
    }
    // Keep at least one entry: evicting the entry we were just asked to store
    // would defeat the point of caching the file the user is looking at now.
    while (entries.size > 1 && bytes() > maxBytes) {
      const oldest = entries.keys().next().value;
      if (oldest === undefined) return;
      entries.delete(oldest);
    }
  }

  return {
    set(key, entry) {
      // Re-insert so a refreshed entry counts as most recently used.
      entries.delete(key);
      entries.set(key, entry);
      trim();
    },
    get(key) {
      const entry = entries.get(key);
      if (!entry) return undefined;
      entries.delete(key);
      entries.set(key, entry);
      return entry;
    },
    drop(key) {
      entries.delete(key);
    },
    clear() {
      entries.clear();
    },
    size: () => entries.size,
    bytes,
  };
}

const defaultCache = createPreviewCache();

/** Remember preview bytes for a target. */
export function cachePreview(key: string, entry: CachedPreview): void {
  defaultCache.set(key, entry);
}

/** Retrieve preview bytes for a target, if still held. */
export function getCachedPreview(key: string): CachedPreview | undefined {
  return defaultCache.get(key);
}

/** Drop one target's bytes (the file behind it was deleted). */
export function dropCachedPreview(key: string): void {
  defaultCache.drop(key);
}

/** Drop every cached preview (identity change / provider unmount). */
export function clearCachedPreviews(): void {
  defaultCache.clear();
}

/** Sizes, for diagnostics and tests. */
export function previewCacheStats(): { size: number; bytes: number } {
  return { size: defaultCache.size(), bytes: defaultCache.bytes() };
}
