import { useEffect, useState } from "react";
import { Download } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import {
  isTextPreviewOversize,
  previewKind,
  previewMimeType,
  previewUnavailableMessage,
  type PreviewKind,
} from "@/lib/filePreview";
import { cachePreview, getCachedPreview, previewCacheKey } from "@/lib/previewCache";

/** What the modal has to show once the bytes have arrived. */
type Loaded =
  | { kind: "rendered"; preview: "image" | "pdf" | "video" | "audio"; url: string }
  | { kind: "text"; text: string }
  | { kind: "unavailable"; reason: string };

type PreviewState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; loaded: Loaded };

/**
 * How long after closing to release the preview's object URL.
 *
 * It must outlast `Modal`'s 180ms exit animation. While that animation runs the
 * panel is still mounted and its `src` still points at this URL, so revoking
 * any earlier makes the browser request a blob that no longer exists — measured
 * as a `console.error` ("Failed to load resource: net::ERR_FILE_NOT_FOUND") on
 * every single image and PDF preview close, 3 runs out of 3. Waiting a few times
 * the animation length fixed that: 0 console errors over the same 3 cycles.
 *
 * Chromium's built-in PDF viewer can still record a *network-level*
 * `requestfailed` for the blob as its frame is torn down, which no page code can
 * see or handle. Holding the blob for the lifetime of the session to silence a
 * log line would trade a real memory leak for a cosmetic one, so the release is
 * deferred rather than cancelled.
 */
const PREVIEW_EXIT_MS = 500;

/**
 * Re-label the fetched bytes with the content type the file's *name* implies.
 *
 * A `<blob:>` URL takes its type from the Blob, so whatever the API called the
 * file is what the `<img>`/`<audio>`/`<video>`/`<iframe>` is handed. That value
 * comes from object storage and is routinely meaningless — measured on the live
 * stack as `application/x-www-form-urlencoded` for every file uploaded below
 * the 100 MiB multipart threshold. `previewMimeType` derives the honest type
 * from the file name and returns `""` when it cannot, which leaves the element
 * sniffing the bytes rather than being told a lie.
 *
 * `new Blob` over an existing Blob does not copy the bytes (the new Blob is a
 * reference to the same data), so this costs nothing for a large file.
 */
function retypeForPreview(
  blob: Blob,
  file: { file_name: string; mime_type?: string | null },
  kind: PreviewKind,
): Blob {
  const type = previewMimeType(file.file_name, kind, file.mime_type);
  return blob.type === type ? blob : new Blob([blob], { type });
}

/**
 * Where a preview's bytes and its Download action come from.
 *
 * Two sources because a conversion output is not a library file: their ids live
 * in different spaces, so a job id sent to the library endpoints answers `404
 * File not found` (and both are UUIDs, so that failure reads as "deleted file"
 * rather than "wrong endpoint").
 */
export type PreviewTarget =
  | { kind: "library"; id: string; file_name: string; mime_type?: string | null }
  | { kind: "job"; id: string; file_name: string; mime_type?: string | null };

/**
 * In-page preview of a library file (opened from the file card's ⋮ menu) or of a
 * completed conversion's output (opened from the Convert page's row).
 *
 * WHY the object URL is created inside an effect: `URL.createObjectURL` pins the
 * Blob in memory until it is revoked, so the effect that creates it also owns
 * revoking it (and does so before a replacement is created, and whenever the
 * modal closes or the target changes). A URL created during render or left in
 * state would leak for the lifetime of the tab. The release is deferred past the
 * exit animation for the reason given at the cleanup.
 *
 * NOTE: this repo has **no jsdom**, so none of the above can be exercised by a
 * unit test — that is exactly why the classification rules (including the
 * oversize guard that keeps a 200 MB log out of the DOM) live in
 * `@/lib/filePreview` and are tested there.
 */
export function FilePreviewModal({
  open,
  onClose,
  target,
}: {
  open: boolean;
  onClose: () => void;
  target: PreviewTarget | null;
}) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  // Bumped by "Try again" so the fetch effect re-runs without re-opening.
  const [attempt, setAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);

  // The effect keys off these primitives, never off `target`.
  //
  // WHY: callers pass an inline object literal (`target={{ kind: "job", … }}`),
  // so a `target` dependency changes identity on every render and re-runs the
  // effect — refetching the bytes and minting a new object URL each time, which
  // both leaks the previous URL and flickers the preview. Each field below is a
  // string or `null`, so React compares it by value and the effect re-runs only
  // when something the preview actually depends on changed.
  const targetKind = target?.kind ?? null;
  const targetId = target?.id ?? null;
  const targetFileName = target?.file_name ?? null;
  const targetMimeType = target?.mime_type ?? null;

  useEffect(() => {
    if (!open || targetKind === null || targetId === null) return;

    // Every async continuation checks this before touching state: closing the
    // modal mid-fetch must not write a result into an unmounted dialog (and
    // must not create an object URL that nothing will ever revoke).
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ status: "loading" });

    void (async () => {
      try {
        // The name the bytes are actually stored under. For a conversion it comes
        // back with the bytes, not from the prop: a converter may emit a
        // container (a multi-page `pdf -> jpg` produces a `.zip`), and the row's
        // own name can be stale before its terminal event arrives — classifying
        // that `.zip` as the image the row claims would put a broken preview on
        // screen.
        //
        // The cache is consulted first: measured on the running app, opening the
        // same file three times downloaded it three times. Bytes under one id
        // are immutable (a re-upload creates a new row and object key), so a hit
        // is safe, and the stored `fileName` — not this render's prop — is what
        // the bytes are classified by on a hit.
        const cacheKey = previewCacheKey(targetKind, targetId, targetFileName ?? "");
        const cached = getCachedPreview(cacheKey);

        let blob: Blob;
        let fileName: string;
        if (cached) {
          blob = cached.blob;
          fileName = cached.fileName;
        } else if (targetKind === "library") {
          blob = await client.fetchLibraryFileBlob(targetId);
          fileName = targetFileName ?? "";
          cachePreview(cacheKey, { blob, fileName });
        } else {
          const output = await client.fetchConversionOutput(targetId);
          blob = output.blob;
          fileName = output.filename;
          cachePreview(cacheKey, { blob, fileName });
        }
        if (cancelled) return;

        const kind = previewKind(fileName, targetMimeType);

        if (kind === "text" && !isTextPreviewOversize(blob.size)) {
          // Decoded as text rather than pointed at by an object URL: nothing
          // renders a raw text blob, it has to become a string.
          const text = await blob.text();
          if (cancelled) return;
          setState({ status: "ready", loaded: { kind: "text", text } });
          return;
        }

        if (kind === "none" || kind === "text") {
          // `text` reaching here means the oversize guard tripped.
          setState({
            status: "ready",
            loaded: { kind: "unavailable", reason: previewUnavailableMessage(kind) },
          });
          return;
        }

        objectUrl = URL.createObjectURL(
          retypeForPreview(blob, { file_name: fileName, mime_type: targetMimeType }, kind),
        );
        setState({ status: "ready", loaded: { kind: "rendered", preview: kind, url: objectUrl } });
      } catch (err) {
        if (cancelled) return;
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "Could not load this file",
        });
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) {
        const url = objectUrl;
        objectUrl = null;
        // Deferred, not immediate. `Modal` keeps its panel mounted for the
        // 180ms exit animation, so the <img>/<iframe> is still alive and still
        // holding this URL when the effect cleans up — revoking it there made
        // Chromium re-request the dead blob and log
        // `requestfailed GET blob:… net::ERR_FILE_NOT_FOUND` plus a matching
        // console error on EVERY image and PDF preview close (measured 3/3
        // open→close cycles). Waiting past the exit animation releases the
        // memory just the same, without the phantom request.
        window.setTimeout(() => URL.revokeObjectURL(url), PREVIEW_EXIT_MS);
      }
    };
  }, [open, attempt, client, targetKind, targetId, targetFileName, targetMimeType]);

  async function download() {
    if (!target || downloading) return;
    setDownloading(true);
    try {
      if (target.kind === "library") {
        await client.downloadLibraryFile(target.id, target.file_name);
      } else {
        // Filename omitted deliberately: `downloadConvertedFile` re-reads the job
        // and derives the name from the object the worker actually produced, so a
        // multi-page `pdf -> jpg` still saves the `.zip` it really is even when
        // this row's name is stale.
        await client.downloadConvertedFile(target.id);
      }
    } catch (err) {
      // Surfaced rather than swallowed: a preview the browser cannot inline is
      // only a dead end if the download also fails silently.
      toastError(err instanceof Error ? err.message : "Could not download this file");
    } finally {
      setDownloading(false);
    }
  }

  const loaded = state.status === "ready" ? state.loaded : null;

  /**
   * The browser refused to render the bytes we handed it.
   *
   * Classification is optimistic on purpose — it can only see the file's name,
   * so it promises a player for anything that *is* audio. Whether this browser
   * can decode that particular container/codec is a separate question it cannot
   * answer: Chromium ships no AAC, so an `.m4a` or `.aac` voice memo is
   * classified `audio` and then silently fails to load. Without this the user
   * was left with a dead control and no explanation; the failure is invisible
   * because a media element that cannot start simply does nothing.
   *
   * Reuses the `unavailable` state rather than adding a second failure copy, so
   * an undecodable file lands on the same "Download it to open it" panel a file
   * we never claimed to preview would.
   *
   * Guarded on the current state being a rendered preview: an error from a
   * stale element (the file changed under us) must not clobber a good one, and
   * must not fire twice.
   */
  function handleRenderError() {
    setState((prev) =>
      prev.status === "ready" && prev.loaded.kind === "rendered"
        ? {
            status: "ready",
            loaded: {
              kind: "unavailable",
              reason: previewUnavailableMessage(prev.loaded.preview),
            },
          }
        : prev,
    );
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Preview"
      description={target?.file_name}
      maxWidth="max-w-3xl"
    >
      <div className="space-y-1">
        {state.status === "loading" && (
          <div role="status">
            <span className="sr-only">Loading preview…</span>
            <Skeleton className="h-[50vh] w-full" />
          </div>
        )}

        {state.status === "error" && (
          <div role="alert" className="rounded-lg border border-outline bg-surface-variant p-4">
            <p className="text-sm text-on-background">{state.message}</p>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => setAttempt((n) => n + 1)}
            >
              Try again
            </Button>
          </div>
        )}

        {loaded?.kind === "unavailable" && (
          <p className="rounded-lg border border-outline bg-surface-variant p-4 text-sm text-muted">
            {loaded.reason}
          </p>
        )}

        {loaded?.kind === "rendered" && loaded.preview === "image" && (
          <img
            src={loaded.url}
            alt={target?.file_name ?? "File preview"}
            className="max-h-[60vh] w-full object-contain"
            // A format this browser cannot paint (HEIC and TIFF are advertised
            // as images here but are not decodable everywhere) otherwise leaves
            // a broken-image glyph in place of any explanation.
            onError={handleRenderError}
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "pdf" && (
          // A browser without an inline PDF viewer renders this as a blank
          // frame, which is why the Download button below is not optional.
          <iframe
            src={loaded.url}
            title={target?.file_name ?? "PDF preview"}
            className="h-[70vh] w-full rounded-lg border border-outline bg-surface-variant"
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "video" && (
          <video
            src={loaded.url}
            controls
            title={target?.file_name ?? "Video preview"}
            className="max-h-[60vh] w-full"
            onError={handleRenderError}
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "audio" && (
          <div className="space-y-2">
            <audio
              src={loaded.url}
              controls
              title={target?.file_name ?? "Audio preview"}
              className="w-full"
              onError={handleRenderError}
            />
            {/* The one place the name is repeated: the audio controls show no
                file name of their own, and the dialog header truncates it. */}
            <p className="truncate text-sm text-muted">{target?.file_name}</p>
          </div>
        )}

        {loaded?.kind === "text" && (
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-outline bg-surface-variant p-3 font-mono text-xs text-on-background">
            {loaded.text}
          </pre>
        )}

        {/* Always present: some types cannot be rendered inline by every
            browser (PDF on mobile), so the download is the guaranteed exit. */}
        <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
          <Button
            variant="secondary"
            onClick={() => void download()}
            disabled={!target || downloading}
          >
            <Download size={16} /> Download
          </Button>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}
