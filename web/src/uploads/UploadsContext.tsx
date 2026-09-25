import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import {
  LEGACY_UPLOADS_STORAGE_KEY,
  cancelUpload as cancelUploadState,
  dismissUpload as dismissUploadState,
  enqueueUploads,
  failUpload,
  limitRefusal,
  nextUploadsToStart,
  progressTick,
  readStoredUploads,
  removeStoredUploads,
  requeueUpload,
  serializeUploads,
  succeedUpload,
  uploadStorageKey,
  type UiUpload,
  type UploadLimits,
} from "@/lib/uploadStore";
import {
  MAX_CONCURRENT_FILE_UPLOADS,
  UploadsContext,
  type UploadsContextValue,
} from "@/uploads/uploadsContext";
import {
  isAbortError,
  isRetryableFailure,
  runUpload,
  uploadErrorMessage,
} from "@/uploads/uploadEngine";
import { putWithXhr } from "@/uploads/uploadTransport";

/**
 * Provides the background upload queue, app-wide and above the router.
 *
 * Mounted next to `JobsProvider` so an upload survives navigation and the
 * bottom-right dock is available on every page — the user's "should be able to
 * run in background" requirement. Like the job store it is remounted whenever
 * the signed-in identity changes, so one account's uploads can never appear in
 * another's session.
 */
export function UploadsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const previousId = useRef<number | null | undefined>(undefined);

  // Drop the unscoped pre-scoping cache instead of adopting it (unknown owner).
  useEffect(() => {
    try {
      localStorage.removeItem(LEGACY_UPLOADS_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  // Once an identity is gone, discard its cache so a shared browser does not
  // retain one account's upload list for the next one to sign in.
  useEffect(() => {
    const previous = previousId.current;
    previousId.current = userId;
    if (previous === undefined || previous === userId) return;
    if (previous !== null) removeStoredUploads(previous, localStorage);
  }, [userId]);

  return (
    <UploadsStore key={userId ?? "anon"} userId={userId}>
      {children}
    </UploadsStore>
  );
}

function UploadsStore({ userId, children }: { userId: number | null; children: ReactNode }) {
  const [uploads, setUploads] = useState<UiUpload[]>(() => readStoredUploads(userId, localStorage));
  const [limits, setLimits] = useState<UploadLimits>({
    maxFileSizeBytes: null,
    availableBytes: null,
  });

  // The bytes themselves. Never persisted and never in React state — a `File`
  // has no business in a re-render path, and localStorage could not hold it.
  const filesRef = useRef<Map<string, File>>(new Map());
  const controllersRef = useRef<Map<string, AbortController>>(new Map());
  // Ids with an engine call in flight, so the queue pump cannot start one twice.
  const runningRef = useRef<Set<string>>(new Set());
  // Latest uploads for callbacks that must read state without re-creating
  // themselves on every progress tick.
  const uploadsRef = useRef<UiUpload[]>(uploads);
  const limitsRef = useRef<UploadLimits>(limits);
  const mountedRef = useRef(true);

  useEffect(() => {
    uploadsRef.current = uploads;
  }, [uploads]);

  useEffect(() => {
    limitsRef.current = limits;
  }, [limits]);

  // Persist metadata only. `serializeUploads` picks the fields, so a future
  // refactor cannot accidentally start writing blobs to disk.
  //
  // Guarded by a signature of the fields that actually matter for a restore
  // (identity of the row, its status, session and error). Without it this wrote
  // the whole list on every progress event — several times a second per file —
  // which is pure localStorage thrash: byte counts are deliberately NOT restored
  // (an interrupted upload does not resume, and a finished one is at its size).
  const persistentSignatureRef = useRef<string | null>(null);
  useEffect(() => {
    const signature = uploads
      .map((upload) => `${upload.id}:${upload.status}:${upload.uploadId ?? ""}:${upload.error ?? ""}`)
      .join("|");
    if (signature === persistentSignatureRef.current) return;
    persistentSignatureRef.current = signature;
    try {
      localStorage.setItem(uploadStorageKey(userId), serializeUploads(uploads));
    } catch {
      /* ignore quota */
    }
  }, [uploads, userId]);

  const refreshLimits = useCallback(() => {
    if (!api.isAuthenticated()) return;
    api
      .dashboard()
      .then((dashboard) => {
        if (!mountedRef.current) return;
        setLimits({
          maxFileSizeBytes: dashboard.storage_stats.max_file_size_bytes ?? null,
          availableBytes: dashboard.storage_stats.available_bytes ?? null,
        });
      })
      .catch(() => {
        // Leave the previous numbers in place. An unavailable dashboard must not
        // disable uploads: the pre-check is advisory, and the server's 413 is
        // the real gate.
      });
  }, []);

  useEffect(() => {
    refreshLimits();
  }, [refreshLimits, userId]);

  const patch = useCallback((id: string, update: (upload: UiUpload) => UiUpload) => {
    setUploads((prev) => prev.map((upload) => (upload.id === id ? update(upload) : upload)));
  }, []);

  const start = useCallback(
    (queued: UiUpload) => {
      const file = filesRef.current.get(queued.id);
      if (!file) {
        // Only reachable for a hydrated `queued` row (its bytes are gone) or a
        // retry after the blob was released.
        patch(queued.id, (upload) =>
          failUploadWith(
            upload,
            "This file is no longer available in the browser. Select it again to upload.",
            false,
          ),
        );
        return;
      }
      if (runningRef.current.has(queued.id)) return;
      runningRef.current.add(queued.id);

      const controller = new AbortController();
      controllersRef.current.set(queued.id, controller);
      patch(queued.id, (upload) => ({ ...upload, status: "uploading", error: null }));

      runUpload({
        client: api,
        transport: {
          put: (url, body, onProgress, signal) => putWithXhr(url, body, { onProgress, signal }),
        },
        file,
        folderId: queued.folderId,
        signal: controller.signal,
        onSession: (session) =>
          patch(queued.id, (upload) => ({
            ...upload,
            uploadId: session.uploadId,
            uploadMode: session.mode,
            partSizeBytes: session.partSizeBytes,
            partCount: session.partCount,
          })),
        onProgress: (bytesDone, etaSeconds) =>
          setUploads((prev) => progressTick(prev, queued.id, { bytesDone, etaSeconds })),
        onVerifying: () => patch(queued.id, (upload) => ({ ...upload, status: "verifying" })),
      })
        .then(() => {
          if (!mountedRef.current) return;
          setUploads((prev) => succeedUpload(prev, queued.id));
          // The bytes are on the server now; the browser copy is only needed for
          // a retry, and a completed upload cannot be retried from here.
          filesRef.current.delete(queued.id);
          // The account's free space changed, so the next pre-check uses the
          // same numbers the Dashboard would show.
          refreshLimits();
        })
        .catch((error: unknown) => {
          if (!mountedRef.current) return;
          if (isAbortError(error) || controller.signal.aborted) {
            setUploads((prev) => cancelUploadState(prev, queued.id));
            return;
          }
          const message = uploadErrorMessage(error);
          const retryable = isRetryableFailure(error);
          setUploads((prev) => failUpload(prev, queued.id, message, retryable));
        })
        .finally(() => {
          runningRef.current.delete(queued.id);
          controllersRef.current.delete(queued.id);
        });
    },
    [patch, refreshLimits],
  );

  // The queue pump. Runs whenever the list changes and starts as many queued
  // rows as there is capacity for. The decision itself is the pure
  // `nextUploadsToStart` (unit-tested in `lib/uploadStore.test.ts`) because the
  // bug it prevents — a multi-file drop deadlocking at "Waiting to start"
  // with no request ever made — came from counting `queued` rows as running
  // when computing capacity. No timer involved: this is what keeps the provider
  // free of anything that could outlive it.
  useEffect(() => {
    const ids = nextUploadsToStart(uploads, runningRef.current, MAX_CONCURRENT_FILE_UPLOADS);
    if (ids.length === 0) return;
    const byId = new Map(uploads.map((upload) => [upload.id, upload]));
    for (const id of ids) {
      const upload = byId.get(id);
      if (upload) start(upload);
    }
  }, [uploads, start]);

  // Abort everything on unmount. The store remounts on identity change, so this
  // is what stops the previous account's transfers (and their callbacks) from
  // continuing into a store that no longer belongs to them.
  useEffect(() => {
    mountedRef.current = true;
    const controllers = controllersRef.current;
    const files = filesRef.current;
    return () => {
      mountedRef.current = false;
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      files.clear();
    };
  }, []);

  const addFiles = useCallback((files: FileList | File[], folderId: string | null) => {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;

    const createdAt = new Date().toISOString();
    const created: UiUpload[] = [];
    // Running total of what this drop will consume, so several files are
    // checked against the free space together rather than one at a time.
    let projectedBytes = 0;

    for (const file of incoming) {
      const id = createUploadId();
      const refusal = limitRefusal(file, projectedBytes, limitsRef.current);
      if (refusal) {
        // Recorded as a failed row rather than a toast: the modal has already
        // closed by the time this runs, and the dock is where the user looks.
        // Not retryable — the file itself is the problem.
        created.push({
          id,
          fileName: file.name,
          size: file.size,
          status: "failed",
          bytesDone: 0,
          folderId,
          createdAt,
          error: refusal,
          retryable: false,
          etaSeconds: null,
        });
        continue;
      }
      projectedBytes += file.size;
      filesRef.current.set(id, file);
      created.push({
        id,
        fileName: file.name,
        size: file.size,
        status: "queued",
        bytesDone: 0,
        folderId,
        createdAt,
        error: null,
        retryable: false,
        etaSeconds: null,
      });
    }

    setUploads((prev) => enqueueUploads(prev, created));
  }, []);

  const cancel = useCallback(
    (id: string) => {
      const upload = uploadsRef.current.find((row) => row.id === id);
      // Mark it first so the row stops moving immediately, then abort the
      // request and tell the server to drop the session (an orphaned multipart
      // upload would otherwise sit in the bucket forever).
      setUploads((prev) => cancelUploadState(prev, id));
      controllersRef.current.get(id)?.abort();
      if (upload?.uploadId) {
        api.cancelUpload(upload.uploadId).catch(() => {
          /* the session expires on its own; nothing actionable for the user */
        });
      }
      filesRef.current.delete(id);
    },
    [],
  );

  const retry = useCallback(
    (id: string) => {
      if (!filesRef.current.has(id)) {
        patch(id, (upload) =>
          failUploadWith(
            upload,
            "This file is no longer available in the browser. Select it again to upload.",
            false,
          ),
        );
        return;
      }
      setUploads((prev) => requeueUpload(prev, id));
    },
    [patch],
  );

  const dismiss = useCallback((id: string) => {
    controllersRef.current.get(id)?.abort();
    controllersRef.current.delete(id);
    filesRef.current.delete(id);
    setUploads((prev) => dismissUploadState(prev, id));
  }, []);

  const value = useMemo<UploadsContextValue>(
    () => ({ uploads, addFiles, cancel, retry, dismiss, limits, refreshLimits }),
    [uploads, addFiles, cancel, retry, dismiss, limits, refreshLimits],
  );

  return <UploadsContext.Provider value={value}>{children}</UploadsContext.Provider>;
}

/** `failUpload` bound to one row, for the `patch` helper's signature. */
function failUploadWith(upload: UiUpload, message: string, retryable: boolean): UiUpload {
  return failUpload([upload], upload.id, message, retryable)[0];
}

/** A collision-free id; the server's `upload_id` arrives later. */
function createUploadId(): string {
  const cryptoObject = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cryptoObject && typeof cryptoObject.randomUUID === "function") {
    return cryptoObject.randomUUID();
  }
  return `u-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
