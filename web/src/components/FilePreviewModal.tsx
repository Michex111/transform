import { useEffect, useState } from "react";
import { Download } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import {
  isTextPreviewOversize,
  previewKind,
  previewUnavailableMessage,
} from "@/lib/filePreview";

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
 * In-page preview of a library file, opened from the file card's ⋮ menu.
 *
 * WHY the object URL is created inside an effect: `URL.createObjectURL` pins the
 * Blob in memory until it is revoked, so the effect that creates it also owns
 * revoking it (and does so before a replacement is created, and whenever the
 * modal closes or the file changes). A URL created during render or left in
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
  file,
}: {
  open: boolean;
  onClose: () => void;
  file: { id: string; file_name: string; mime_type?: string | null } | null;
}) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const [state, setState] = useState<PreviewState>({ status: "loading" });
  // Bumped by "Try again" so the fetch effect re-runs without re-opening.
  const [attempt, setAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    if (!open || !file) return;

    // Every async continuation checks this before touching state: closing the
    // modal mid-fetch must not write a result into an unmounted dialog (and
    // must not create an object URL that nothing will ever revoke).
    let cancelled = false;
    let objectUrl: string | null = null;
    setState({ status: "loading" });

    void (async () => {
      try {
        const blob = await client.fetchLibraryFileBlob(file.id);
        if (cancelled) return;

        const kind = previewKind(file.file_name, file.mime_type);

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

        objectUrl = URL.createObjectURL(blob);
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
  }, [open, attempt, client, file]);

  async function download() {
    if (!file || downloading) return;
    setDownloading(true);
    try {
      await client.downloadLibraryFile(file.id, file.file_name);
    } catch (err) {
      // Surfaced rather than swallowed: a preview the browser cannot inline is
      // only a dead end if the download also fails silently.
      toastError(err instanceof Error ? err.message : "Could not download this file");
    } finally {
      setDownloading(false);
    }
  }

  const loaded = state.status === "ready" ? state.loaded : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Preview"
      description={file?.file_name}
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
            alt={file?.file_name ?? "File preview"}
            className="max-h-[60vh] w-full object-contain"
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "pdf" && (
          // A browser without an inline PDF viewer renders this as a blank
          // frame, which is why the Download button below is not optional.
          <iframe
            src={loaded.url}
            title={file?.file_name ?? "PDF preview"}
            className="h-[70vh] w-full rounded-lg border border-outline bg-surface-variant"
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "video" && (
          <video
            src={loaded.url}
            controls
            title={file?.file_name ?? "Video preview"}
            className="max-h-[60vh] w-full"
          />
        )}

        {loaded?.kind === "rendered" && loaded.preview === "audio" && (
          <div className="space-y-2">
            <audio src={loaded.url} controls className="w-full" />
            {/* The one place the name is repeated: the audio controls show no
                file name of their own, and the dialog header truncates it. */}
            <p className="truncate text-sm text-muted">{file?.file_name}</p>
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
          <Button variant="secondary" onClick={() => void download()} disabled={!file || downloading}>
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
