/**
 * In-memory cache of uploaded files so a failed job can be re-uploaded without
 * the user re-selecting the file. Keyed by the job id.
 *
 * The cache is a *bounded* LRU: it used to retain every conversion's source
 * `File` for the whole session (only one retry branch ever dropped one), so a
 * batch of large documents — 20 × 80 MB, say — pinned ~1.6 GB in the tab until
 * it was killed. It is also never the only path to a retry: `HistoryPage` falls
 * back to sending the user back to the converter when nothing is cached.
 */

export interface CachedUpload {
  file: File;
  source: string;
  target: string;
}

export interface FileCacheLimits {
  /** Most entries retained. */
  maxFiles: number;
  /**
   * Most bytes retained. A single entry larger than this is still kept (it is
   * the file the user just chose), so the bound applies from the second entry.
   */
  maxBytes: number;
}

export const DEFAULT_FILE_CACHE_LIMITS: FileCacheLimits = {
  maxFiles: 8,
  // 512 MB: several large conversions still retry without a re-select, while a
  // pathological batch cannot exhaust a tab's memory.
  maxBytes: 512 * 1024 * 1024,
};

export interface FileCache {
  /** Remember a file against a job id (used by the retry fallback). */
  cacheFileForJob(jobId: string, upload: CachedUpload): void;
  /** Retrieve a cached file for a job id, if still in this session. */
  getCachedFile(jobId: string): CachedUpload | undefined;
  /** Drop a cached file (e.g. after a successful re-upload). */
  dropCachedFile(jobId: string): void;
  /** Drop everything (identity change / provider unmount). */
  clear(): void;
  /** Entries currently retained. */
  size(): number;
  /** Total bytes currently retained. */
  bytes(): number;
}

/** Create an independent bounded file cache. */
export function createFileCache(limits: Partial<FileCacheLimits> = {}): FileCache {
  const maxFiles = limits.maxFiles ?? DEFAULT_FILE_CACHE_LIMITS.maxFiles;
  const maxBytes = limits.maxBytes ?? DEFAULT_FILE_CACHE_LIMITS.maxBytes;

  // `Map` iteration order is insertion order, so re-inserting an entry on read
  // makes the first key the least recently used one.
  const cache = new Map<string, CachedUpload>();

  function bytes(): number {
    let total = 0;
    for (const entry of cache.values()) total += entry.file.size;
    return total;
  }

  function trim(): void {
    while (cache.size > maxFiles) {
      const oldest = cache.keys().next().value;
      if (oldest === undefined) return;
      cache.delete(oldest);
    }
    // Keep at least one entry: dropping the entry we were just asked to store
    // would make the cache useless for the file the user is working with now.
    while (cache.size > 1 && bytes() > maxBytes) {
      const oldest = cache.keys().next().value;
      if (oldest === undefined) return;
      cache.delete(oldest);
    }
  }

  return {
    cacheFileForJob(jobId, upload) {
      // Re-insert so a refreshed entry counts as most recently used.
      cache.delete(jobId);
      cache.set(jobId, upload);
      trim();
    },
    getCachedFile(jobId) {
      const entry = cache.get(jobId);
      if (!entry) return undefined;
      cache.delete(jobId);
      cache.set(jobId, entry);
      return entry;
    },
    dropCachedFile(jobId) {
      cache.delete(jobId);
    },
    clear() {
      cache.clear();
    },
    size: () => cache.size,
    bytes,
  };
}

/**
 * Whether a job's cached input may be released now that it is done.
 *
 * ONLY a successful conversion releases its file. `HistoryPage.handleRetry`
 * re-uploads a *failed* job's cached input when the input object is gone from
 * storage, so dropping on `FAILED` would send the user back to the converter to
 * re-select a file we already hold — the exact case the cache exists for.
 */
export function releaseCachedFile(status: string): boolean {
  return status === "COMPLETED";
}

const defaultCache = createFileCache();

/** Remember a file against a job id (used by the retry fallback). */
export function cacheFileForJob(jobId: string, upload: CachedUpload): void {
  defaultCache.cacheFileForJob(jobId, upload);
}

/** Retrieve a cached file for a job id, if still in this session. */
export function getCachedFile(jobId: string): CachedUpload | undefined {
  return defaultCache.getCachedFile(jobId);
}

/** Drop a cached file (e.g. after a successful re-upload). */
export function dropCachedFile(jobId: string): void {
  defaultCache.dropCachedFile(jobId);
}

/** Drop every cached file (used when the signed-in identity changes/unmounts). */
export function clearCachedFiles(): void {
  defaultCache.clear();
}

