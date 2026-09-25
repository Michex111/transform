/**
 * The upload engine: session creation, single-PUT vs multipart transfer,
 * part-URL batching, bounded concurrency, and per-part retry.
 *
 * Deliberately framework-free and dependency-injected (`client`, `transport`,
 * clock), so the rules that matter — how many sockets an upload may open, when a
 * part is retried, what is sent to `verify` — are unit-testable against mocks.
 * The React provider is a thin wrapper around this, and the local API does not
 * have to exist for its behaviour to be pinned down.
 */

import {
  FILE_TOO_LARGE,
  STORAGE_QUOTA_EXCEEDED,
  type CreateUploadSessionRequest,
  type UploadMode,
  type UploadPartCompletion,
  type UploadResponse,
  type UploadSession,
  type UploadSessionPartsResponse,
  type VerifyUploadRequest,
} from "@/api/types";
import { fileNameExtension } from "@/lib/format";
import { createEtaTracker, uploadBytesFromParts } from "@/lib/uploadEta";
import { abortError, isAbortError, type PutResult } from "@/uploads/uploadTransport";

export { isAbortError };

/** The subset of the API client the engine uses. `api` satisfies it as-is. */
export interface UploadsClient {
  createUploadSession(body: CreateUploadSessionRequest): Promise<UploadResponse>;
  createUploadSessionParts(
    uploadId: string,
    partNumbers: number[],
  ): Promise<UploadSessionPartsResponse>;
  verifyUpload(
    uploadId: string,
    jobId?: string,
    body?: VerifyUploadRequest,
  ): Promise<UploadSession>;
  cancelUpload(uploadId: string): Promise<void>;
}

/** The byte-moving half, injectable so tests never touch the network. */
export interface UploadTransport {
  put(
    url: string,
    body: Blob,
    onProgress: (loaded: number, total: number) => void,
    signal: AbortSignal,
  ): Promise<PutResult>;
}

/**
 * How many part PUTs may be in flight at once.
 *
 * A 5 GB upload is ~80 parts, and opening a socket per part starves every other
 * request the page makes — which is exactly the "every other feature should
 * work as expected" requirement failing. Three keeps the pipe busy without
 * monopolising the connection pool.
 */
export const DEFAULT_MAX_PARALLEL_PARTS = 3;

/**
 * How many part URLs to mint per `/parts` call.
 *
 * Minting lazily in batches (rather than every URL up front) is what stops a
 * long upload from depending on URLs that expire while later parts are still
 * queued: each batch is used almost immediately after it is signed.
 */
export const DEFAULT_PART_BATCH_SIZE = 20;

/** Backoff between retries of one part PUT, in milliseconds. */
export const DEFAULT_PART_RETRY_DELAYS_MS: readonly number[] = [500, 1500, 4000];

/** A failure the caller can describe to the user, with a retry hint. */
export class UploadFlowError extends Error {
  readonly retryable: boolean;

  constructor(message: string, retryable = true) {
    super(message);
    this.name = "UploadFlowError";
    this.retryable = retryable;
  }
}

/** True for the server's structured size/quota refusals, which never retry. */
export function isQuotaError(error: unknown): boolean {
  const code = (error as { code?: unknown } | null)?.code;
  return code === FILE_TOO_LARGE || code === STORAGE_QUOTA_EXCEEDED;
}

/** A user-facing message for anything the engine throws. */
export function uploadErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Upload failed";
}

/** True when a failure may be retried without the user re-selecting the file. */
export function isRetryableFailure(error: unknown): boolean {
  if (error instanceof UploadFlowError) return error.retryable;
  return !isQuotaError(error);
}

export interface UploadSessionInfo {
  uploadId: string;
  mode: UploadMode;
  partSizeBytes: number | null;
  partCount: number | null;
}

export interface RunUploadParams {
  client: UploadsClient;
  transport: UploadTransport;
  file: File;
  folderId: string | null;
  signal: AbortSignal;
  /** Bytes done (whole file) plus the latest smoothed estimate. */
  onProgress: (bytesDone: number, etaSeconds: number | null) => void;
  /** The session exists; the row can record the server's plan. */
  onSession?: (info: UploadSessionInfo) => void;
  /** Every part is up and the authoritative verify call is next. */
  onVerifying?: () => void;
  maxParallelParts?: number;
  partBatchSize?: number;
  partRetryDelaysMs?: readonly number[];
  sleep?: (ms: number) => Promise<void>;
}

/**
 * Move one file's bytes.
 *
 * Resolves once the server's `verify` call has succeeded — the object is not
 * real until then — and rejects with the underlying error otherwise. An aborted
 * `signal` rejects with an `AbortError`.
 */
export async function runUpload(params: RunUploadParams): Promise<void> {
  const { client, transport, file, folderId, signal } = params;
  const sleep = params.sleep ?? defaultSleep;
  const partRetryDelaysMs = params.partRetryDelaysMs ?? DEFAULT_PART_RETRY_DELAYS_MS;

  throwIfAborted(signal);

  const eta = createEtaTracker();
  const report = (bytesDone: number) => {
    eta.sample(bytesDone, Date.now());
    params.onProgress(bytesDone, eta.remainingSeconds(file.size));
  };
  report(0);

  const session = await client.createUploadSession({
    file_extension: fileNameExtension(file.name),
    file_name: file.name,
    folder_id: folderId,
    // The server uses this to choose single-PUT vs multipart. Omitting it (as
    // the convert flows do) keeps their proven single-object path.
    file_size: file.size,
  });

  params.onSession?.({
    uploadId: session.upload_id,
    mode: session.upload_mode,
    partSizeBytes: session.part_size_bytes,
    partCount: session.part_count,
  });
  throwIfAborted(signal);

  const partSizeBytes = session.part_size_bytes;
  const multipart =
    session.upload_mode === "multipart" &&
    typeof partSizeBytes === "number" &&
    partSizeBytes > 0;

  let parts: UploadPartCompletion[] | undefined;

  if (multipart) {
    parts = await runMultipart({
      client,
      transport,
      file,
      session,
      partSizeBytes: partSizeBytes as number,
      signal,
      report,
      sleep,
      delays: partRetryDelaysMs,
      maxParallelParts: params.maxParallelParts ?? DEFAULT_MAX_PARALLEL_PARTS,
      partBatchSize: params.partBatchSize ?? DEFAULT_PART_BATCH_SIZE,
    });
  } else {
    if (!session.upload_url) {
      throw new UploadFlowError("The server did not return an upload URL for this file.");
    }
    await putWithRetry({
      transport,
      url: session.upload_url,
      blob: file,
      signal,
      delays: partRetryDelaysMs,
      sleep,
      onLoaded: (loaded) => report(loaded),
    });
    report(file.size);
  }

  params.onVerifying?.();

  // The verify call is the authority: it assembles a multipart object (hence
  // the ETags) and its 413 is what the user sees for an over-quota file. When
  // the store did not expose ETags there is nothing to send, so the body is
  // omitted and the server's own answer decides — an older API that never
  // wanted a body keeps working.
  const body: VerifyUploadRequest | undefined = parts && parts.length > 0 ? { parts } : undefined;
  await client.verifyUpload(session.upload_id, undefined, body);
}

interface MultipartParams {
  client: UploadsClient;
  transport: UploadTransport;
  file: File;
  session: UploadResponse;
  partSizeBytes: number;
  signal: AbortSignal;
  report: (bytesDone: number) => void;
  sleep: (ms: number) => Promise<void>;
  delays: readonly number[];
  maxParallelParts: number;
  partBatchSize: number;
}

/**
 * Upload a file as parts and return the ETags `verify` needs.
 *
 * Parts are claimed from a shared counter by a fixed pool of workers, so
 * concurrency is bounded no matter how many parts the file has. Each part's
 * progress is aggregated into the whole-file figure the caller renders.
 */
async function runMultipart(params: MultipartParams): Promise<UploadPartCompletion[]> {
  const {
    client,
    transport,
    file,
    session,
    partSizeBytes,
    signal,
    report,
    sleep,
    delays,
    maxParallelParts,
    partBatchSize,
  } = params;

  const totalParts =
    session.part_count && session.part_count > 0
      ? session.part_count
      : Math.max(1, Math.ceil(file.size / partSizeBytes));

  const urls = new Map<number, string>();
  const batchRequests = new Map<number, Promise<void>>();
  const completedBytesByPart = new Map<number, number>();
  const loadedByPart = new Map<number, number>();
  const etags = new Map<number, string>();

  const aggregate = () => {
    let completedBytes = 0;
    for (const bytes of completedBytesByPart.values()) completedBytes += bytes;
    let inFlightLoadedBytes = 0;
    for (const bytes of loadedByPart.values()) inFlightLoadedBytes += bytes;
    return uploadBytesFromParts({ completedBytes, inFlightLoadedBytes, size: file.size });
  };

  /**
   * The presigned URL for a part, minting its batch if necessary.
   *
   * Concurrent workers share one in-flight request per batch (keyed by batch
   * index) so a burst of workers cannot mint the same batch several times over.
   */
  async function mintUrl(partNumber: number): Promise<string> {
    const cached = urls.get(partNumber);
    if (cached) return cached;

    const batchIndex = Math.floor((partNumber - 1) / partBatchSize);
    let pending = batchRequests.get(batchIndex);
    if (!pending) {
      const first = batchIndex * partBatchSize + 1;
      const last = Math.min(totalParts, first + partBatchSize - 1);
      const numbers: number[] = [];
      for (let part = first; part <= last; part++) numbers.push(part);
      pending = client.createUploadSessionParts(session.upload_id, numbers).then((response) => {
        for (const part of response.parts) urls.set(part.part_number, part.url);
      });
      batchRequests.set(batchIndex, pending);
    }
    await pending;

    const url = urls.get(partNumber);
    if (!url) {
      throw new UploadFlowError(
        `The server did not return an upload URL for part ${partNumber}.`,
      );
    }
    return url;
  }

  let nextPart = 1;

  async function worker(): Promise<void> {
    for (;;) {
      throwIfAborted(signal);
      const partNumber = nextPart;
      if (partNumber > totalParts) return;
      // Claimed synchronously before any await, so two workers can never take
      // the same part.
      nextPart = partNumber + 1;

      const url = await mintUrl(partNumber);
      const start = (partNumber - 1) * partSizeBytes;
      const end = Math.min(start + partSizeBytes, file.size);

      const result = await putWithRetry({
        transport,
        url,
        blob: file.slice(start, end),
        signal,
        delays,
        sleep,
        onLoaded: (loaded) => {
          loadedByPart.set(partNumber, loaded);
          report(aggregate());
        },
      });

      if (result.etag) etags.set(partNumber, result.etag);
      loadedByPart.delete(partNumber);
      completedBytesByPart.set(partNumber, end - start);
      report(aggregate());
    }
  }

  const concurrency = Math.max(1, Math.min(maxParallelParts, totalParts));
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  report(file.size);

  return [...etags.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([part_number, etag]) => ({ part_number, etag }));
}

interface PutWithRetryParams {
  transport: UploadTransport;
  url: string;
  blob: Blob;
  signal: AbortSignal;
  delays: readonly number[];
  sleep: (ms: number) => Promise<void>;
  onLoaded: (loaded: number) => void;
}

/**
 * PUT one blob, retrying with backoff.
 *
 * A 5 GB upload that dies on part 71 must not restart from part 1, so a single
 * transient failure is retried in place with the same presigned URL (which is
 * still valid — batches are minted shortly before use). Only a persistent
 * failure, or an abort, leaves this function.
 */
async function putWithRetry(params: PutWithRetryParams): Promise<PutResult> {
  const { transport, url, blob, signal, delays, sleep, onLoaded } = params;

  for (let attempt = 0; ; attempt++) {
    throwIfAborted(signal);
    // A retry restarts this part's own byte count; zeroing it keeps the
    // aggregate honest instead of leaving the previous attempt's high-water
    // mark on screen.
    onLoaded(0);
    try {
      return await transport.put(url, blob, onLoaded, signal);
    } catch (error) {
      if (isAbortError(error) || signal.aborted) throw abortError();
      if (attempt >= delays.length) throw error;
      await sleep(delays[attempt]);
    }
  }
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw abortError();
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
