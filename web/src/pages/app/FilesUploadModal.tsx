import { useRef, useState } from "react";
import { CircleNotch, FilePlus, UploadSimple } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Modal } from "@/components/Modal";
import { Button } from "@/components/ui";
import { fileNameExtension, formatBytes } from "@/lib/format";

/** Mirrors the advertised tier upload limit shown across the UI (100 MB). */
const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

export function FilesUploadModal({
  open,
  onClose,
  folderId,
  onUploaded,
}: {
  open: boolean;
  onClose: () => void;
  folderId: string | null;
  /** Called after a successful upload+verify so the parent can refresh the list. */
  onUploaded: () => void;
}) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function reset() {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  function close() {
    if (busy) return;
    reset();
    onClose();
  }

  function pick(f: File | undefined | null) {
    if (!f) return;
    if (f.size > MAX_UPLOAD_BYTES) {
      error(`"${f.name}" is too large. The maximum upload size is 100 MB.`);
      return;
    }
    setFile(f);
  }

  async function upload() {
    if (!file || busy) return;
    const ext = fileNameExtension(file.name);
    setBusy(true);
    try {
      // 1. Open a presigned upload session scoped to the current folder.
      const session = await client.createUploadSession({
        file_extension: ext,
        file_name: file.name,
        folder_id: folderId,
      });
      // 2. PUT the bytes (no Content-Type header — the URL is signed without one).
      await client.putToPresignedUrl(session.upload_url, file);
      // 3. Verify completion — finalizes the file record in the library.
      await client.verifyUpload(session.upload_id);
      success(`"${file.name}" uploaded to your library.`);
      reset();
      onClose();
      onUploaded();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not upload file");
    } finally {
      setBusy(false);
    }
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
          onClick={() => !busy && inputRef.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !busy) {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={(e) => { e.preventDefault(); if (!busy) setDragOver(true); }}
          onDragLeave={(e) => {
            e.preventDefault();
            if (e.currentTarget.contains(e.relatedTarget as Node)) return;
            setDragOver(false);
          }}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (!busy) pick(e.dataTransfer.files?.[0]);
          }}
          aria-label="Choose a file to upload"
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
            dragOver ? "border-primary bg-primary/5" : "border-outline-strong hover:border-primary/50"
          } ${busy ? "pointer-events-none opacity-60" : ""}`}
        >
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-primary-container text-primary">
            <UploadSimple size={24} weight="duotone" />
          </span>
          {file ? (
            <div className="flex items-center gap-2">
              <FilePlus size={18} className="text-primary" />
              <span className="max-w-xs truncate text-sm font-medium text-on-background">{file.name}</span>
              <span className="text-xs text-muted">({formatBytes(file.size)})</span>
            </div>
          ) : (
            <>
              <p className="text-sm font-semibold text-on-background">Drag &amp; drop a file here</p>
              <p className="text-xs text-muted">or click to browse — up to 100 MB</p>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            onChange={(e) => pick(e.target.files?.[0])}
            aria-hidden
            tabIndex={-1}
          />
        </div>

        {/* Loading state */}
        {busy && (
          <div className="flex items-center gap-2">
            <motion.span
              className="flex h-1.5 flex-1 overflow-hidden rounded-full bg-outline"
              aria-hidden
            >
              <motion.span
                className="h-full rounded-full bg-primary"
                animate={{ x: ["-100%", "400%"] }}
                transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
              />
            </motion.span>
            <span className="inline-flex items-center gap-1.5 text-sm text-muted">
              <CircleNotch size={15} className="animate-spin text-primary" />
              Uploading…
            </span>
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={upload} disabled={!file || busy}>
            <UploadSimple size={16} /> {busy ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
