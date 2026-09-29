import { useEffect, useId, useRef, useState } from "react";
import {
  CaretRight,
  CircleNotch,
  FileArrowUp,
  FolderOpen,
  FolderSimple,
  House,
  MagnifyingGlass,
  Paperclip,
  WarningCircle,
} from "@phosphor-icons/react";
import type { AssistantAttachment, FileMetadataResponse, FolderResponse } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Modal } from "@/components/Modal";
import { FormatThumb } from "@/components/FormatThumb";
import { Button, Skeleton } from "@/components/ui";
import { formatBytes } from "@/lib/format";
import {
  MAX_ASSISTANT_ATTACHMENTS,
  attachmentExtension,
  attachmentFromFile,
  canAttachMore,
} from "@/lib/assistantAttachments";
import { putWithXhr } from "@/uploads/uploadTransport";

type UploadState =
  | { status: "idle" }
  | { status: "uploading"; fileName: string; percent: number | null }
  | { status: "error"; message: string };

/**
 * How the dialog finds a file: a flat search over the account's files, or the
 * drive's own folder structure.
 *
 * Both exist because they answer different questions. Search is faster when you
 * know the name; the drive browser is the only way to reach a file you have
 * filed away in a folder and can only recognise by where it lives. The flat
 * list is also root-only (`listFiles()`), so without this the picker simply
 * could not attach a file that sits inside a folder.
 */
type AttachView = "search" | "drive";

/**
 * The composer's paperclip control.
 *
 * One dialog carries every way to attach a file: "upload from this device" (a
 * hidden `<input type="file">` driven through the progress-capable XHR
 * transport), "search your files" (the account's library, filtered by name) and
 * "Drive" (browse the folder structure and pick a file where it lives). A single
 * dialog rather than a menu that opens a second dialog keeps it to one focus
 * trap and one Escape target, and sidesteps the repo's `position: fixed`
 * containment trap entirely.
 *
 * The cap is enforced here as well as in the parent: at the limit the library
 * rows and the upload button are disabled with an explanation, so nothing looks
 * broken when one more file will not fit. The cap itself comes from the caller
 * (`maxAttachments`, the plan's own limit) so the copy and the disabled state
 * always name the real number.
 */
export function AttachControl({
  attachments,
  onAttach,
  disabled = false,
  maxAttachments = MAX_ASSISTANT_ATTACHMENTS,
}: {
  attachments: AssistantAttachment[];
  onAttach: (attachment: AssistantAttachment) => void;
  disabled?: boolean;
  /**
   * The caller's plan cap (from `/assistant/status`), defaulting to the client
   * constant so an older API or the mini chat keeps the previous behaviour.
   */
  maxAttachments?: number;
}) {
  const { api: client } = useAuth();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [files, setFiles] = useState<FileMetadataResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [upload, setUpload] = useState<UploadState>({ status: "idle" });

  // ---- Drive browser ----
  const [view, setView] = useState<AttachView>("search");
  // The trail inside the drive; empty means the root ("My Drive"), which has no
  // folder object of its own.
  const [drivePath, setDrivePath] = useState<FolderResponse[]>([]);
  const [driveFolders, setDriveFolders] = useState<FolderResponse[]>([]);
  const [driveFiles, setDriveFiles] = useState<FileMetadataResponse[]>([]);
  const [driveLoading, setDriveLoading] = useState(false);
  const [driveFailed, setDriveFailed] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const searchId = useId();
  const atCap = !canAttachMore(attachments, maxAttachments);
  const attachedIds = new Set(attachments.map((attachment) => attachment.id));
  const driveFolderId = drivePath[drivePath.length - 1]?.id ?? null;

  // Load the library when the dialog opens (and refresh when it reopens).
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setLoadFailed(false);
    client
      .listFiles()
      .then((result) => {
        if (active) setFiles(result.files);
      })
      .catch(() => {
        if (!active) return;
        setFiles([]);
        setLoadFailed(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [open, client]);

  // Reset the search, the browser position and any stale upload state each time
  // the dialog opens — a reopened dialog must not resume a half-finished upload
  // or land the user back in a folder they explored last time.
  useEffect(() => {
    if (open) {
      setQuery("");
      setView("search");
      setDrivePath([]);
      setUpload({ status: "idle" });
    }
  }, [open]);

  // Load the drive level being browsed. Deliberately lazy: only a user who
  // actually opens the Drive tab pays for this request.
  useEffect(() => {
    if (!open || view !== "drive") return;
    let active = true;
    setDriveLoading(true);
    setDriveFailed(false);
    const load = async () => {
      try {
        let nextFolders: FolderResponse[] = [];
        let nextFiles: FileMetadataResponse[] = [];
        if (driveFolderId != null) {
          const contents = await client.getFolderContents(driveFolderId);
          nextFolders = contents.folders;
          nextFiles = contents.files;
        } else {
          const [folderList, fileList] = await Promise.all([
            client.listFolders(),
            client.listFiles(),
          ]);
          nextFolders = folderList.folders;
          nextFiles = fileList.files;
        }
        if (!active) return;
        setDriveFolders(nextFolders);
        setDriveFiles(nextFiles);
      } catch {
        // Not a dead end: the other tab and the upload path still work, so the
        // message says exactly that rather than implying the dialog is broken.
        if (!active) return;
        setDriveFolders([]);
        setDriveFiles([]);
        setDriveFailed(true);
      } finally {
        if (active) setDriveLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [open, view, driveFolderId, client]);

  // One rule for every list in the dialog: a case-insensitive substring of the
  // name, with a blank query matching everything.
  const needle = query.trim().toLowerCase();
  const matches = (name: string) => needle === "" || name.toLowerCase().includes(needle);

  const filtered = files.filter((file) => matches(file.file_name));
  const filteredDriveFolders = driveFolders.filter((folder) => matches(folder.name));
  const filteredDriveFiles = driveFiles.filter((file) => matches(file.file_name));

  function selectLibraryFile(file: FileMetadataResponse) {
    onAttach(attachmentFromFile(file));
    setOpen(false);
  }

  async function uploadFromDevice(file: File) {
    if (!canAttachMore(attachments, maxAttachments)) return;
    // `attachmentExtension` never returns an empty string, so a dotless file
    // ("LICENSE") still gets a usable extension for the upload session.
    const extension = attachmentExtension({ id: "", name: file.name });
    setUpload({ status: "uploading", fileName: file.name, percent: null });
    try {
      const session = await client.createUploadSession({
        file_extension: extension,
        file_name: file.name,
        file_size: file.size,
      });
      if (!session.upload_url) {
        throw new Error("This file is too large to attach here. Upload it from the Files page instead.");
      }
      await putWithXhr(session.upload_url, file, {
        onProgress: (loaded, total) => {
          const percent = total > 0 ? Math.round((loaded / total) * 100) : null;
          setUpload({ status: "uploading", fileName: file.name, percent });
        },
      });
      const verified = await client.verifyUpload(session.upload_id);
      if (!verified.file_id) {
        throw new Error("The upload finished but no file id came back. Try attaching it from your files.");
      }
      onAttach({ id: verified.file_id, name: file.name, extension });
      setUpload({ status: "idle" });
      setOpen(false);
    } catch (err) {
      // `ApiError.message` is the server's own wording (e.g. the 413 quota
      // explanation), so it is shown verbatim rather than replaced with a
      // generic failure.
      setUpload({
        status: "error",
        message: err instanceof Error ? err.message : "That upload didn't finish. Try again.",
      });
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={disabled}
        aria-label="Attach a file"
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-variant hover:text-on-background disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
      >
        <Paperclip size={18} />
      </button>

      {/* Rendered outside the dialog on purpose: `display: none` keeps it out of
          the tab order and out of the dialog's initial-focus scan, while
          `.click()` still opens the picker. */}
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        disabled={atCap}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) void uploadFromDevice(file);
        }}
      />

      <Modal open={open} onClose={() => setOpen(false)} title="Attach a file">
        <div className="space-y-4">
          {atCap ? (
            <p className="flex items-start gap-2 rounded-lg border border-outline bg-surface-variant/50 px-3 py-2 text-sm text-muted">
              <WarningCircle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden />
              You can attach up to {maxAttachments} {maxAttachments === 1 ? "file" : "files"}.
              Remove one to add another.
            </p>
          ) : (
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => inputRef.current?.click()}
              disabled={upload.status === "uploading"}
            >
              {upload.status === "uploading" ? (
                <CircleNotch size={16} className="animate-spin" />
              ) : (
                <FileArrowUp size={16} />
              )}
              {upload.status === "uploading" ? "Uploading…" : "Upload from this device"}
            </Button>
          )}

          {upload.status === "uploading" && (
            <div className="space-y-1">
              <p className="truncate text-xs text-muted" title={upload.fileName}>
                {upload.fileName}
              </p>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-variant">
                <div
                  className="h-full rounded-full bg-primary transition-[width] duration-200"
                  style={{ width: `${upload.percent ?? 0}%` }}
                />
              </div>
            </div>
          )}

          {upload.status === "error" && (
            <p role="alert" className="flex items-start gap-2 text-sm text-error">
              <WarningCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
              {upload.message}
            </p>
          )}

          <div className="space-y-2">
            {/* Two ways to reach a file. A pressed-state toggle rather than
                tab ARIA: these are two views of one list, and the repo uses
                `aria-pressed` on plain buttons for exactly this (see
                `FormatPicker`) instead of half-implementing a tablist. */}
            <div className="flex gap-1 rounded-lg border border-outline bg-surface-variant/40 p-1">
              <button
                type="button"
                onClick={() => setView("search")}
                aria-pressed={view === "search"}
                className={`inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors pointer-coarse:min-h-11 ${
                  view === "search"
                    ? "bg-surface text-on-background shadow-sm"
                    : "text-muted hover:text-on-background"
                }`}
              >
                <MagnifyingGlass size={14} aria-hidden /> Search
              </button>
              <button
                type="button"
                onClick={() => setView("drive")}
                aria-pressed={view === "drive"}
                className={`inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors pointer-coarse:min-h-11 ${
                  view === "drive"
                    ? "bg-surface text-on-background shadow-sm"
                    : "text-muted hover:text-on-background"
                }`}
              >
                <FolderOpen size={15} aria-hidden /> Drive
              </button>
            </div>

            <label htmlFor={searchId} className="sr-only">
              {view === "drive" ? "Search this folder" : "Search your files"}
            </label>
            <div className="flex items-center gap-2 rounded-lg border border-outline bg-surface-variant/50 px-3 py-2 focus-within:border-primary">
              <MagnifyingGlass size={15} className="shrink-0 text-muted" aria-hidden />
              <input
                id={searchId}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={view === "drive" ? "Search this folder" : "Search your files"}
                className="w-full bg-transparent text-sm text-on-background placeholder:text-muted focus:outline-none"
              />
            </div>

            {/* Where the user is in the drive. Shown at the root too, so the
                structure is legible before anything is opened. */}
            {view === "drive" && (
              <div className="flex items-center gap-1 overflow-x-auto rounded-lg border border-outline bg-surface-variant/50 px-3 py-2 text-sm">
                <button
                  type="button"
                  onClick={() => setDrivePath([])}
                  className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors ${
                    drivePath.length === 0
                      ? "font-medium text-on-background"
                      : "text-muted hover:bg-surface-variant hover:text-on-background"
                  }`}
                >
                  <House size={14} aria-hidden /> My Drive
                </button>
                {drivePath.map((folder, i) => (
                  <span key={folder.id} className="inline-flex items-center">
                    <CaretRight size={12} className="text-muted" aria-hidden />
                    <button
                      type="button"
                      onClick={() => setDrivePath((prev) => prev.slice(0, i + 1))}
                      className={`rounded-md px-1.5 py-0.5 transition-colors ${
                        i === drivePath.length - 1
                          ? "font-medium text-on-background"
                          : "text-muted hover:bg-surface-variant hover:text-on-background"
                      }`}
                    >
                      {folder.name}
                    </button>
                  </span>
                ))}
              </div>
            )}

            <div className="max-h-72 min-h-40 overflow-y-auto rounded-xl border border-outline">
              {view === "drive" ? (
                driveLoading ? (
                  <div className="space-y-2 p-3">
                    {Array.from({ length: 4 }).map((_, i) => (
                      <Skeleton key={i} className="h-11 w-full" />
                    ))}
                  </div>
                ) : driveFailed ? (
                  <EmptyState
                    icon={<WarningCircle size={24} className="text-error" />}
                    message="Your drive couldn't be opened. You can still search or upload."
                  />
                ) : filteredDriveFolders.length === 0 && filteredDriveFiles.length === 0 ? (
                  <EmptyState
                    icon={<FolderOpen size={24} className="text-muted" />}
                    message={
                      needle !== ""
                        ? "Nothing here matches that search."
                        : drivePath.length === 0
                          ? "Your drive is empty — upload a file to get started."
                          : "This folder is empty."
                    }
                  />
                ) : (
                  <ul className="p-1.5">
                    {/* Folders first: they are the way deeper in. */}
                    {filteredDriveFolders.map((folder) => (
                      <li key={folder.id}>
                        <button
                          type="button"
                          onClick={() => setDrivePath((prev) => [...prev, folder])}
                          className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-surface-variant pointer-coarse:min-h-11"
                        >
                          <FolderSimple
                            size={18}
                            weight="duotone"
                            className="shrink-0 text-warning"
                            aria-hidden
                          />
                          <span className="min-w-0 flex-1 truncate text-on-background">
                            {folder.name}
                          </span>
                          <CaretRight size={13} className="shrink-0 text-muted" aria-hidden />
                        </button>
                      </li>
                    ))}
                    {filteredDriveFiles.map((file) => (
                      <li key={file.id}>
                        <AttachFileRow
                          file={file}
                          disabled={atCap || attachedIds.has(file.id)}
                          attached={attachedIds.has(file.id)}
                          onSelect={() => selectLibraryFile(file)}
                        />
                      </li>
                    ))}
                  </ul>
                )
              ) : loading ? (
                <div className="space-y-2 p-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-11 w-full" />
                  ))}
                </div>
              ) : loadFailed ? (
                <EmptyState
                  icon={<WarningCircle size={24} className="text-error" />}
                  message="Your files couldn't be loaded. You can still upload from this device."
                />
              ) : filtered.length === 0 ? (
                <EmptyState
                  icon={<Paperclip size={24} className="text-muted" />}
                  message={
                    files.length === 0 ? "You don't have any files yet." : "No files match that search."
                  }
                />
              ) : (
                <ul className="p-1.5">
                  {filtered.map((file) => (
                    <li key={file.id}>
                      <AttachFileRow
                        file={file}
                        disabled={atCap || attachedIds.has(file.id)}
                        attached={attachedIds.has(file.id)}
                        onSelect={() => selectLibraryFile(file)}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {view === "drive" && (
              <p className="px-1 text-xs text-muted">
                {drivePath.length === 0
                  ? "Browsing the top level of your drive."
                  : `You are inside ${drivePath[drivePath.length - 1].name}.`}
              </p>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
}

/** The centred "nothing to show" panel every list in this dialog uses. */
function EmptyState({ icon, message }: { icon: React.ReactNode; message: string }) {
  return (
    <div className="flex flex-col items-center gap-1.5 px-4 py-10 text-center">
      {icon}
      <p className="text-sm text-muted">{message}</p>
    </div>
  );
}

/**
 * One selectable file, shared by the search list and the drive browser so the
 * two cannot drift apart in how they look or what they disable.
 */
function AttachFileRow({
  file,
  disabled,
  attached,
  onSelect,
}: {
  file: FileMetadataResponse;
  disabled: boolean;
  attached: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-surface-variant disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11"
    >
      <FormatThumb
        format={attachmentExtension({ id: "", name: file.file_name })}
        size="sm"
        label=""
        className="shrink-0"
      />
      <span className="min-w-0 flex-1 truncate text-on-background">{file.file_name}</span>
      <span className="shrink-0 text-xs text-muted">
        {attached ? "Attached" : formatBytes(file.file_size_bytes)}
      </span>
    </button>
  );
}
