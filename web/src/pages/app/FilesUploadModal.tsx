import { useRef, useState } from "react";
import { FilePlus, UploadSimple, X } from "@phosphor-icons/react";
import { Modal } from "@/components/Modal";
import { Button } from "@/components/ui";
import { formatBytes } from "@/lib/format";
import { limitRefusal } from "@/lib/uploadStore";
import { useUploads } from "@/uploads/uploadsContext";

/**
 * Fallback cap, used only while (or if) the API does not publish
 * `max_file_size_bytes`.
 *
 * It is the limit this modal enforced before, which is the safe direction to
 * err: an older API is never told a file is acceptable that it will reject. The
 * server's own number replaces it as soon as it is available, which is where
 * the raised limit comes from.
 */
const FALLBACK_MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

/** Stable identity for a picked file, used for de-duping and React keys. */
function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/**
 * Pick files and hand them to the background upload manager.
 *
 * This dialog deliberately does no transferring of its own: it closes the moment
 * the files are queued, and the dock (bottom-right) reports progress, the time
 * estimate and cancellation. The previous version blocked the dialog on a fake
 * indeterminate bar until the upload finished — exactly the behaviour this
 * feature removes.
 */
export function FilesUploadModal({
  open,
  onClose,
  folderId,
}: {
  open: boolean;
  onClose: () => void;
  folderId: string | null;
}) {
  const { addFiles, limits } = useUploads();
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const maxFileSizeBytes = limits.maxFileSizeBytes ?? FALLBACK_MAX_UPLOAD_BYTES;

  function reset() {
    setFiles([]);
    if (inputRef.current) inputRef.current.value = "";
  }

  function close() {
    reset();
    onClose();
  }

  /** Append newly picked files, ignoring exact repeats from a second pick. */
  function pick(picked: FileList | File[] | null | undefined) {
    const incoming = Array.from(picked ?? []);
    if (incoming.length === 0) return;
    setFiles((prev) => {
      const seen = new Set(prev.map(fileKey));
      const added = incoming.filter((file) => !seen.has(fileKey(file)));
      return added.length > 0 ? [...prev, ...added] : prev;
    });
  }

  function removeAt(key: string) {
    setFiles((prev) => prev.filter((file) => fileKey(file) !== key));
  }

  // Files the client already knows are too large are filtered out here so one
  // bad pick cannot hold up the batch. The manager applies the same per-file
  // check plus the cumulative free-space one when it queues what is sent.
  const uploadable = files.filter(
    (file) => limitRefusal(file, 0, { ...limits, availableBytes: null }) === null,
  );

  function startUpload() {
    if (uploadable.length === 0) return;
    addFiles(uploadable, folderId);
    // Close immediately: the transfer continues in the background, and the dock
    // is the surface that reports it.
    close();
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Upload to library"
      description="Add a file to the current folder"
      maxWidth="max-w-lg"
    >
      <div className="space-y-4">
        {/* Drop zone + file browser */}
        <div
          role="button"
          tabIndex={0}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (e.currentTarget.contains(e.relatedTarget as Node)) return;
            setDragOver(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            pick(e.dataTransfer.files);
          }}
          aria-label="Choose files to upload"
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
            dragOver ? "border-primary bg-primary/5" : "border-outline-strong hover:border-primary/50"
          }`}
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-container text-primary">
            <UploadSimple size={24} weight="duotone" />
          </span>
          <p className="text-sm font-semibold text-on-background">Drag &amp; drop files here</p>
          <p className="text-xs text-muted">
            or click to browse — up to {formatBytes(maxFileSizeBytes)} each
          </p>
          <input
            ref={inputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              pick(e.target.files);
              // Clear the value so re-picking the same file fires `change` again.
              e.target.value = "";
            }}
            aria-hidden
            tabIndex={-1}
          />
        </div>

        {files.length > 0 && (
          <ul className="max-h-48 space-y-1 overflow-y-auto" aria-label="Files to upload">
            {files.map((file) => {
              const key = fileKey(file);
              const refusal = limitRefusal(file, 0, { ...limits, availableBytes: null });
              return (
                <li
                  key={key}
                  className="flex items-center gap-2 rounded-lg border border-outline bg-surface-variant px-3 py-2"
                >
                  <FilePlus size={16} className="shrink-0 text-primary" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-on-background" title={file.name}>
                      {file.name}
                    </span>
                    {refusal ? (
                      <span className="block text-xs text-error">{refusal}</span>
                    ) : (
                      <span className="block text-xs text-muted">{formatBytes(file.size)}</span>
                    )}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeAt(key)}
                    aria-label={`Remove ${file.name}`}
                    className="-mr-1 shrink-0 rounded-md p-2 text-muted transition-colors hover:bg-surface hover:text-on-background pointer-coarse:p-3"
                  >
                    <X size={16} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button onClick={startUpload} disabled={uploadable.length === 0}>
            <UploadSimple size={16} />
            {uploadable.length > 1 ? `Upload ${uploadable.length} files` : "Upload"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
