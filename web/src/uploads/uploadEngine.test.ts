// Tests for the upload engine, against a mocked API client and transport.
//
// The engine is the one place that decides how many sockets a transfer opens,
// when a failed part is retried, and what is sent to the authoritative `verify`
// call — all of which are hard to observe (and expensive to get wrong) in a
// browser. The API contract is mocked here deliberately: the local backend does
// not implement it yet, and the engine must be correct against the frozen shape
// regardless.

import { describe, expect, it } from "vitest";
import type {
  CreateUploadSessionRequest,
  UploadResponse,
  UploadSession,
  UploadSessionPartsResponse,
  VerifyUploadRequest,
} from "@/api/types";
import { FILE_TOO_LARGE } from "@/api/types";
import {
  DEFAULT_PART_BATCH_SIZE,
  UploadFlowError,
  isAbortError,
  isQuotaError,
  isRetryableFailure,
  runUpload,
  type UploadTransport,
  type UploadsClient,
} from "@/uploads/uploadEngine";
import { abortError, type PutResult } from "@/uploads/uploadTransport";

/** A `File`-shaped object: Node's `File` is not guaranteed, and only these
 *  members are used. Slicing returns real blobs so `Blob.size` is meaningful. */
function fakeFile(name: string, size: number): File {
  const bytes = new Uint8Array(size);
  return {
    name,
    size,
    slice: (start: number, end: number) => new Blob([bytes.slice(start, end)]),
  } as unknown as File;
}

function singleSession(overrides: Partial<UploadResponse> = {}): UploadResponse {
  return {
    upload_id: "srv-1",
    object_key: "uploads/report.pdf",
    upload_url: "https://store.example/single",
    expires_in_minutes: 60,
    upload_mode: "single",
    part_size_bytes: null,
    part_count: null,
    max_file_size_bytes: null,
    ...overrides,
  };
}

function multipartSession(partSize: number, partCount: number): UploadResponse {
  return singleSession({
    upload_url: null,
    upload_mode: "multipart",
    part_size_bytes: partSize,
    part_count: partCount,
  });
}

interface Harness {
  client: UploadsClient;
  transport: UploadTransport;
  sessionRequests: CreateUploadSessionRequest[];
  partRequests: number[][];
  verifyCalls: Array<{ uploadId: string; body?: VerifyUploadRequest }>;
  putUrls: string[];
  sleeps: number[];
  maxConcurrentPuts: number;
}

/**
 * A client + transport pair that respond the way the contract says.
 *
 * `createUploadSession` is scripted; part URLs encode their part number so the
 * transport can report one ETag per part; and the transport tracks how many
 * PUTs overlapped, which is how the concurrency bound is asserted.
 */
function harness(options: {
  sessions: UploadResponse[] | ((request: CreateUploadSessionRequest) => Promise<UploadResponse>);
  putFailureAttempts?: Map<number, number>;
}): Harness {
  const state: Harness = {
    client: undefined as unknown as UploadsClient,
    transport: undefined as unknown as UploadTransport,
    sessionRequests: [],
    partRequests: [],
    verifyCalls: [],
    putUrls: [],
    sleeps: [],
    maxConcurrentPuts: 0,
  };

  let sessionIndex = 0;
  let inFlightPuts = 0;
  const attemptsByPart = new Map<number, number>();

  function partNumberFromUrl(url: string): number {
    const match = /\/p(\d+)$/.exec(url);
    return match ? Number(match[1]) : 0;
  }

  state.client = {
    async createUploadSession(request) {
      state.sessionRequests.push(request);
      if (typeof options.sessions === "function") return options.sessions(request);
      const next = options.sessions[Math.min(sessionIndex, options.sessions.length - 1)];
      sessionIndex += 1;
      return next;
    },
    async createUploadSessionParts(_uploadId, partNumbers): Promise<UploadSessionPartsResponse> {
      state.partRequests.push(partNumbers);
      return {
        parts: partNumbers.map((part_number) => ({
          part_number,
          url: `https://store.example/session/p${part_number}`,
        })),
        expires_in_minutes: 60,
      };
    },
    async verifyUpload(uploadId, _jobId, body): Promise<UploadSession> {
      state.verifyCalls.push({ uploadId, body });
      return {
        upload_id: uploadId,
        object_key: "uploads/report.pdf",
        status: "COMPLETED",
        file_name: "report.pdf",
        folder_id: null,
      };
    },
    async cancelUpload() {
      /* nothing to abort in the mock */
    },
  };

  state.transport = {
    async put(url, body, onProgress, signal): Promise<PutResult> {
      const part = partNumberFromUrl(url);
      state.putUrls.push(url);
      inFlightPuts += 1;
      state.maxConcurrentPuts = Math.max(state.maxConcurrentPuts, inFlightPuts);
      try {
        // Yield so concurrent PUTs genuinely overlap.
        await new Promise((resolve) => setTimeout(resolve, 2));
        onProgress(body.size, body.size);
        if (signal.aborted) throw abortError();

        const attempt = (attemptsByPart.get(part) ?? 0) + 1;
        attemptsByPart.set(part, attempt);
        const failUntil = options.putFailureAttempts?.get(part);
        if (failUntil !== undefined && attempt <= failUntil) {
          throw new Error(`transient failure on part ${part}`);
        }
        return { etag: `etag-${part}` };
      } finally {
        inFlightPuts -= 1;
      }
    },
  };

  return state;
}

const noSleep = () => Promise.resolve();
const trackSleep = (state: Harness) => (ms: number) => {
  state.sleeps.push(ms);
  return Promise.resolve();
};

describe("runUpload — single PUT", () => {
  it("requests a session with the file size and verifies without a parts body", async () => {
    const state = harness({ sessions: [singleSession()] });
    const progress: number[] = [];

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("report.pdf", 300),
      folderId: "folder-1",
      signal: new AbortController().signal,
      onProgress: (bytesDone) => progress.push(bytesDone),
      sleep: noSleep,
    });

    expect(state.sessionRequests).toEqual([
      {
        file_extension: "pdf",
        file_name: "report.pdf",
        folder_id: "folder-1",
        file_size: 300,
      },
    ]);
    expect(state.partRequests).toEqual([]);
    expect(state.putUrls).toEqual(["https://store.example/single"]);
    expect(state.verifyCalls).toHaveLength(1);
    expect(state.verifyCalls[0].uploadId).toBe("srv-1");
    // No parts body for a single-PUT session — the exact shape the older API
    // expects.
    expect(state.verifyCalls[0].body).toBeUndefined();
    expect(progress[progress.length - 1]).toBe(300);
  });

  it("fails clearly when a single-PUT session carries no URL", async () => {
    const state = harness({ sessions: [singleSession({ upload_url: null })] });
    await expect(
      runUpload({
        client: state.client,
        transport: state.transport,
        file: fakeFile("report.pdf", 10),
        folderId: null,
        signal: new AbortController().signal,
        onProgress: () => {},
        sleep: noSleep,
      }),
    ).rejects.toThrow(UploadFlowError);
  });

  it("reports the session plan so the row can record it", async () => {
    const state = harness({ sessions: [singleSession()] });
    const sessions: unknown[] = [];
    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("report.pdf", 10),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: () => {},
      onSession: (info) => sessions.push(info),
      sleep: noSleep,
    });
    expect(sessions).toEqual([
      { uploadId: "srv-1", mode: "single", partSizeBytes: null, partCount: null },
    ]);
  });
});

describe("runUpload — multipart", () => {
  it("uploads every part, aggregates progress, and verifies with the ETags", async () => {
    const state = harness({ sessions: [multipartSession(10, 3)] });
    const progress: number[] = [];

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("big.bin", 25),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: (bytesDone) => progress.push(bytesDone),
      sleep: noSleep,
    });

    // Concurrency means the PUT order is not guaranteed; which parts were
    // uploaded is.
    expect(state.putUrls.slice().sort()).toEqual([
      "https://store.example/session/p1",
      "https://store.example/session/p2",
      "https://store.example/session/p3",
    ]);
    expect(state.verifyCalls[0].body).toEqual({
      parts: [
        { part_number: 1, etag: "etag-1" },
        { part_number: 2, etag: "etag-2" },
        { part_number: 3, etag: "etag-3" },
      ],
    });
    // Progress is monotonic and ends at exactly the file size, even though the
    // last part is a short one.
    expect(progress[progress.length - 1]).toBe(25);
    for (let i = 1; i < progress.length; i++) {
      expect(progress[i]).toBeGreaterThanOrEqual(progress[i - 1]);
    }
  });

  it("mints part URLs in batches rather than all up front", async () => {
    const partCount = DEFAULT_PART_BATCH_SIZE + 5;
    const state = harness({ sessions: [multipartSession(10, partCount)] });

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("huge.bin", partCount * 10),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: () => {},
      partBatchSize: DEFAULT_PART_BATCH_SIZE,
      maxParallelParts: 1,
      sleep: noSleep,
    });

    expect(state.partRequests).toEqual([
      Array.from({ length: DEFAULT_PART_BATCH_SIZE }, (_, i) => i + 1),
      Array.from({ length: 5 }, (_, i) => i + DEFAULT_PART_BATCH_SIZE + 1),
    ]);
  });

  it("keeps concurrent part PUTs within the configured bound", async () => {
    const state = harness({ sessions: [multipartSession(10, 30)] });

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("huge.bin", 300),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: () => {},
      maxParallelParts: 3,
      partBatchSize: 10,
      sleep: noSleep,
    });

    // The whole point: 30 parts must not mean 30 sockets.
    expect(state.maxConcurrentPuts).toBeLessThanOrEqual(3);
    expect(state.maxConcurrentPuts).toBe(3);
    expect(state.putUrls).toHaveLength(30);
  });

  it("retries a failed part with backoff instead of restarting the file", async () => {
    const state = harness({
      sessions: [multipartSession(10, 4)],
      // Part 2 fails on its first attempt only.
      putFailureAttempts: new Map([[2, 1]]),
    });

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("big.bin", 40),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: () => {},
      maxParallelParts: 1,
      partRetryDelaysMs: [500, 1500],
      sleep: trackSleep(state),
    });

    // The backoff was observed with the configured delay...
    expect(state.sleeps).toEqual([500]);
    // ...part 2 was PUT twice, and every other part exactly once (no restart).
    const attempts = state.putUrls.filter((url) => url.endsWith("/p2"));
    expect(attempts).toHaveLength(2);
    expect(state.putUrls.filter((url) => url.endsWith("/p1"))).toHaveLength(1);
    expect(state.verifyCalls[0].body?.parts?.map((part) => part.part_number)).toEqual([1, 2, 3, 4]);
  });

  it("gives up on a part after the retries are exhausted", async () => {
    const state = harness({
      sessions: [multipartSession(10, 2)],
      putFailureAttempts: new Map([[1, 99]]),
    });

    await expect(
      runUpload({
        client: state.client,
        transport: state.transport,
        file: fakeFile("big.bin", 20),
        folderId: null,
        signal: new AbortController().signal,
        onProgress: () => {},
        partRetryDelaysMs: [1, 2],
        maxParallelParts: 1,
        sleep: noSleep,
      }),
    ).rejects.toThrow(/transient failure/);

    expect(state.verifyCalls).toHaveLength(0);
  });

  it("omits the parts body when the store did not expose ETags", async () => {
    const state = harness({ sessions: [multipartSession(10, 2)] });
    // A transport that returns no ETag, as a bucket missing
    // `Access-Control-Expose-Headers: ETag` would.
    state.transport = {
      put: async (_url, body, onProgress) => {
        onProgress(body.size, body.size);
        return { etag: null };
      },
    };

    await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("big.bin", 20),
      folderId: null,
      signal: new AbortController().signal,
      onProgress: () => {},
      sleep: noSleep,
    });

    expect(state.verifyCalls[0].body).toBeUndefined();
  });
});

describe("runUpload — failure and cancellation", () => {
  it("propagates the server's 413 refusal unchanged", async () => {
    const refusal = Object.assign(new Error("File exceeds the 5 GB limit."), {
      status: 413,
      code: FILE_TOO_LARGE,
      details: { code: FILE_TOO_LARGE, max_file_size_bytes: 5_000, file_size: 6_000 },
    });
    const state = harness({
      sessions: async () => {
        throw refusal;
      },
    });

    await expect(
      runUpload({
        client: state.client,
        transport: state.transport,
        file: fakeFile("huge.bin", 6_000),
        folderId: null,
        signal: new AbortController().signal,
        onProgress: () => {},
        sleep: noSleep,
      }),
    ).rejects.toBe(refusal);

    // The message the user sees is the server's, and a size refusal is not
    // retryable — retrying it would fail identically.
    expect(isQuotaError(refusal)).toBe(true);
    expect(isRetryableFailure(refusal)).toBe(false);
  });

  it("treats a network failure as retryable", () => {
    expect(isRetryableFailure(new Error("connection reset"))).toBe(true);
    expect(isRetryableFailure(abortError())).toBe(true);
  });

  it("rejects with an AbortError when the signal is aborted mid-flight", async () => {
    const state = harness({ sessions: [multipartSession(10, 5)] });
    state.transport = {
      // Never resolves: only the abort ends this.
      put: (_url, _body, _onProgress, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(abortError()));
        }),
    };

    const controller = new AbortController();
    const promise = runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("big.bin", 50),
      folderId: null,
      signal: controller.signal,
      onProgress: () => {},
      sleep: noSleep,
    });
    controller.abort();

    const error = await promise.catch((thrown: unknown) => thrown);
    expect(isAbortError(error)).toBe(true);
  });

  it("never starts a request for an already-aborted signal", async () => {
    const state = harness({ sessions: [singleSession()] });
    const controller = new AbortController();
    controller.abort();

    const error = await runUpload({
      client: state.client,
      transport: state.transport,
      file: fakeFile("report.pdf", 10),
      folderId: null,
      signal: controller.signal,
      onProgress: () => {},
      sleep: noSleep,
    }).catch((thrown: unknown) => thrown);

    expect(isAbortError(error)).toBe(true);
    expect(state.sessionRequests).toEqual([]);
  });
});
