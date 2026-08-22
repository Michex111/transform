/** In-memory cache of uploaded files so a failed job can be re-uploaded
 *  without the user re-selecting the file. Keyed by the job id. */
interface CachedUpload {
  file: File;
  source: string;
  target: string;
}

const cache = new Map<string, CachedUpload>();

/** Remember a file against a job id (used by the retry fallback). */
export function cacheFileForJob(jobId: string, upload: CachedUpload): void {
  cache.set(jobId, upload);
}

/** Retrieve a cached file for a job id, if still in this session. */
export function getCachedFile(jobId: string): CachedUpload | undefined {
  return cache.get(jobId);
}

/** Drop a cached file (e.g. after a successful re-upload). */
export function dropCachedFile(jobId: string): void {
  cache.delete(jobId);
}
