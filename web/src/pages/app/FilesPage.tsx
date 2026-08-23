import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Item, PopIn, staggerContainer } from "@/lib/motion";
import {
  FolderSimple,
  CaretRight,
  House,
  DotsThreeVertical,
  UploadSimple,
  Plus,
  Trash,
  PenNib,
  Check,
  X,
  Download,
  ArrowsClockwise,
  Star,
  SquaresFour,
  CheckSquare,
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { FileThumbnail } from "@/components/FileThumbnail";
import { downloadFromUrl } from "@/lib/download";
import { formatExt } from "@/lib/format";
import { FilesUploadModal } from "./FilesUploadModal";
import { FilesConvertModal } from "./FilesConvertModal";
import { FilesMassConvertModal } from "./FilesMassConvertModal";
import { FilesMoveModal, type MoveItem } from "./FilesMoveModal";
import type { FolderResponse, FileMetadataResponse } from "@/api/types";

// Custom MIME type used to identify a draggable file in the page's HTML5 DnD.
const MOVE_MIME = "application/x-transform-file";
// Custom MIME type used to identify a draggable folder.
const MOVE_FOLDER_MIME = "application/x-transform-folder";

/** Browse the library as Drive-style file/folder cards with breadcrumb pathing. */
export function FilesPage() {
  const { api: client } = useAuth();
  const { success, error } = useToast();

  // Path = array of folder breadcrumbs; empty array = root.
  const [path, setPath] = useState<FolderResponse[]>([]);
  const [folders, setFolders] = useState<FolderResponse[]>([]);
  const [files, setFiles] = useState<FileMetadataResponse[]>([]);
  const [loading, setLoading] = useState(true);
  // Inline "new folder" card (a folder with an editable name), selected by default.
  const [newFolder, setNewFolder] = useState<{ id: string; name: string } | null>(null);
  const newFolderInput = useRef<HTMLInputElement>(null);
  // Feature modals.
  const [uploadOpen, setUploadOpen] = useState(false);
  const [convertFile, setConvertFile] = useState<FileMetadataResponse | null>(null);
  // Id of the file currently being dragged (null when no drag is active).
  const [draggedFileId, setDraggedFileId] = useState<string | null>(null);
  // Id of the folder currently being dragged (null when no drag is active).
  const [draggedFolderId, setDraggedFolderId] = useState<string | null>(null);
  // Selection mode + selected ids.
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedFileIds, setSelectedFileIds] = useState<Set<string>>(new Set());
  const [selectedFolderIds, setSelectedFolderIds] = useState<Set<string>>(new Set());
  // "Move to…" modal target.
  const [moveTarget, setMoveTarget] = useState<MoveItem | null>(null);
  // Mass-convert modal target.
  const [massConvertOpen, setMassConvertOpen] = useState(false);
  // Styled delete confirmation.
  const [deleteConfirm, setDeleteConfirm] = useState<{
    title: string;
    message: string;
    onConfirm: () => Promise<void> | void;
  } | null>(null);
  // Favorites view toggle.
  const [showFavorites, setShowFavorites] = useState(false);

  const reduce = useReducedMotion();
  const currentFolderId = path[path.length - 1]?.id ?? null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (showFavorites) {
        // Favorites view lists favorited files across all folders; folders are
        // not shown in this cross-folder aggregate view.
        const res = await client.listFavorites(1, 100);
        setFolders([]);
        setFiles(res.files);
      } else if (currentFolderId) {
        const contents = await client.getFolderContents(currentFolderId);
        setFolders(contents.folders);
        setFiles(contents.files);
      } else {
        const [f, fl] = await Promise.all([client.listFolders(), client.listFiles()]);
        setFolders(f.folders);
        setFiles(fl.files);
      }
    } catch (e) {
      error(e instanceof Error ? e.message : "Could not load files");
    } finally {
      setLoading(false);
    }
  }, [client, currentFolderId, error, showFavorites]);

  useEffect(() => {
    load();
  }, [load]);

  // Focus the new-folder name input when it appears.
  useEffect(() => {
    if (newFolder) window.setTimeout(() => newFolderInput.current?.focus(), 60);
  }, [newFolder]);

  async function saveNewFolder(e: FormEvent) {
    e.preventDefault();
    if (!newFolder) return;
    const name = newFolder.name.trim() || "new_folder";
    try {
      const created = await client.createFolder(name, currentFolderId);
      // The placeholder card is NOT part of `folders`, so append the persisted
      // folder (filtering out the placeholder id defensively, then prepend).
      setFolders((prev) => [created, ...prev.filter((f) => f.id !== newFolder.id)]);
      setNewFolder(null);
      success("Folder created");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not create folder");
    }
  }

  function openFolder(folder: FolderResponse) {
    setPath((prev) => [...prev, folder]);
  }

  function goTo(index: number) {
    setPath((prev) => prev.slice(0, index + 1));
  }

  // ---- Selection helpers ----
  function toggleSelectionMode() {
    setSelectionMode((v) => {
      const next = !v;
      if (!next) {
        setSelectedFileIds(new Set());
        setSelectedFolderIds(new Set());
      }
      return next;
    });
  }

  /** Track the last-clicked id for shift-range selection. */
  const lastSelectedRef = useRef<string | null>(null);

  function toggleFile(id: string, shift: boolean) {
    setSelectedFileIds((prev) => {
      const next = new Set(prev);
      if (shift && lastSelectedRef.current) {
        // Select the contiguous range in the current files array.
        const ids = files.map((f) => f.id);
        const a = ids.indexOf(lastSelectedRef.current);
        const b = ids.indexOf(id);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          for (let i = lo; i <= hi; i++) next.add(ids[i]);
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      lastSelectedRef.current = id;
      return next;
    });
  }

  function toggleFolder(id: string, shift: boolean) {
    setSelectedFolderIds((prev) => {
      const next = new Set(prev);
      if (shift && lastSelectedRef.current) {
        const ids = folders.map((f) => f.id);
        const a = ids.indexOf(lastSelectedRef.current);
        const b = ids.indexOf(id);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          for (let i = lo; i <= hi; i++) next.add(ids[i]);
          return next;
        }
      }
      if (next.has(id)) next.delete(id);
      else next.add(id);
      lastSelectedRef.current = id;
      return next;
    });
  }

  function clearSelection() {
    setSelectedFileIds(new Set());
    setSelectedFolderIds(new Set());
    lastSelectedRef.current = null;
  }

  // The selected files (files only, for conversion). Folders are never converted.
  const selectedFiles = useMemo(
    () => files.filter((f) => selectedFileIds.has(f.id)),
    [files, selectedFileIds],
  );
  const selectedCount = selectedFileIds.size + selectedFolderIds.size;
  // Determine the common source format among selected files. All selected files
  // must share the same source extension for the mass-convert toolbar to enable.
  const selectedFileFormats = useMemo(
    () => selectedFiles.map((f) => formatExt(f.file_name, f.mime_type).toLowerCase()),
    [selectedFiles],
  );
  const commonSourceFormat =
    selectedFileFormats.length > 0 && selectedFileFormats.every((x) => x === selectedFileFormats[0])
      ? selectedFileFormats[0]
      : null;

  // ---- Favorites ----
  async function toggleFavorite(file: FileMetadataResponse) {
    const next = !(file.is_favorite ?? false);
    try {
      const updated = await client.setFileFavorite(file.id, next);
      setFiles((prev) => prev.map((f) => (f.id === file.id ? { ...f, is_favorite: updated.is_favorite } : f)));
      success(next ? "Added to favorites" : "Removed from favorites");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not update favorite");
    }
  }

  // ---- Mass operations ----
  async function performBatchDelete() {
    const fileIds = [...selectedFileIds];
    const folderIds = [...selectedFolderIds];
    if (fileIds.length === 0 && folderIds.length === 0) return;
    try {
      const res = await client.batchDelete({ file_ids: fileIds, folder_ids: folderIds });
      success(`Deleted ${res.deleted_files} file${res.deleted_files === 1 ? "" : "s"} and ${res.deleted_folders} folder${res.deleted_folders === 1 ? "" : "s"}.`);
      clearSelection();
      setSelectionMode(false);
      await load();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not delete items");
    }
  }

  function confirmBatchDelete() {
    const fileIds = [...selectedFileIds];
    const folderIds = [...selectedFolderIds];
    const count = fileIds.length + folderIds.length;
    if (count === 0) return;
    setDeleteConfirm({
      title: `Delete ${count} ${count === 1 ? "item" : "items"}?`,
      message:
        folderIds.length > 0
          ? "This also removes everything inside any selected folder. This can't be undone."
          : "This can't be undone.",
      onConfirm: performBatchDelete,
    });
  }

  async function runMassConvert() {
    // The mass-convert modal has already dispatched each job. Here we only
    // finalize the page state: clear the selection and exit selection mode.
    clearSelection();
    setSelectionMode(false);
  }

  // ---- Move to… ----
  async function handleMoveTo(destFolderId: string | null) {
    if (!moveTarget) return;
    try {
      if (moveTarget.kind === "file") {
        await client.moveFile(moveTarget.id, destFolderId);
      } else {
        await client.moveFolder(moveTarget.id, destFolderId);
      }
      success(`${moveTarget.kind === "file" ? "File" : "Folder"} moved`);
      setMoveTarget(null);
      await load();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not move item");
      throw err;
    }
  }

  async function renameFolder(folder: FolderResponse, name: string) {
    const next = name.trim() || folder.name;
    if (next === folder.name) return;
    try {
      const updated = await client.renameFolder(folder.id, next);
      setFolders((prev) => prev.map((f) => (f.id === folder.id ? updated : f)));
      success("Folder renamed");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not rename folder");
    }
  }

  async function deleteFolder(folder: FolderResponse) {
    setDeleteConfirm({
      title: `Delete "${folder.name}"?`,
      message: "This also removes everything inside the folder. This can't be undone.",
      onConfirm: async () => {
        try {
          await client.deleteFolder(folder.id);
          setFolders((prev) => prev.filter((f) => f.id !== folder.id));
          success("Folder deleted");
        } catch (err) {
          error(err instanceof Error ? err.message : "Could not delete folder");
        }
      },
    });
  }

  async function deleteFile(id: string) {
    const file = files.find((f) => f.id === id);
    setDeleteConfirm({
      title: `Delete "${file?.file_name ?? "this file"}"?`,
      message: "This can't be undone.",
      onConfirm: async () => {
        try {
          await client.deleteFile(id);
          setFiles((prev) => prev.filter((f) => f.id !== id));
          success("File deleted");
        } catch (err) {
          error(err instanceof Error ? err.message : "Could not delete file");
        }
      },
    });
  }

  async function downloadFile(file: FileMetadataResponse) {
    try {
      const { download_url } = await client.getFileDownload(file.id);
      if (download_url.startsWith("http")) {
        downloadFromUrl(download_url, file.file_name);
      } else {
        const res = await fetch(download_url, {
          headers: { Authorization: `Bearer ${localStorage.getItem("transform_access_token")}` },
        });
        if (!res.ok) throw new Error(`Download failed (${res.status})`);
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        downloadFromUrl(url, file.file_name);
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not download file");
    }
  }

  async function renameFile(id: string, name: string) {
    const next = name.trim();
    if (!next) return;
    try {
      const updated = await client.renameFile(id, next);
      setFiles((prev) => prev.map((f) => (f.id === id ? updated : f)));
      success("File renamed");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not rename file");
    }
  }

  /** Move a file into a target folder (null = root). No-op if it's already there. */
  async function handleMove(fileId: string, targetFolderId: string | null) {
    if (targetFolderId === currentFolderId) return;
    try {
      await client.moveFile(fileId, targetFolderId);
      success("File moved");
      await load();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not move file");
    } finally {
      setDraggedFileId(null);
    }
  }

  /** Move a folder into a target folder (null = root). No-op if it's already there. */
  async function handleMoveFolder(folderId: string, targetFolderId: string | null) {
    // Guard against dropping a folder into itself or one of its descendants.
    if (targetFolderId === folderId) return;
    if (targetFolderId === currentFolderId) return;
    // Check the current breadcrumb path: dropping onto an ancestor is self/descendant.
    if (path.some((p) => p.id === folderId) && path.some((p) => p.id === targetFolderId)) return;
    try {
      await client.moveFolder(folderId, targetFolderId);
      success("Folder moved");
      await load();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not move folder");
    } finally {
      setDraggedFolderId(null);
    }
  }

  /** Shared drag-over/drop handlers for a drop target that moves to `target`. */
  function makeDropHandlers(target: string | null) {
    const active = draggedFileId !== null || draggedFolderId !== null;
    return {
      onDragOver: (e: DragEvent<HTMLElement>) => {
        if (!active) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      },
      onDrop: (e: DragEvent<HTMLElement>) => {
        if (!active) return;
        e.preventDefault();
        const fileId = e.dataTransfer.getData(MOVE_MIME);
        const folderId = e.dataTransfer.getData(MOVE_FOLDER_MIME);
        if (fileId) void handleMove(fileId, target);
        else if (folderId) void handleMoveFolder(folderId, target);
      },
    };
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">My Drive</h1>
          <p className="text-sm text-muted">Your files, in folders.</p>
          <p
            className="mt-0.5 text-xs text-muted"
            title="Drag a file or folder onto a folder to move it"
          >
            Drag a file or folder onto a folder or breadcrumb to move it
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant={showFavorites ? "primary" : "secondary"}
            onClick={() => {
              setShowFavorites((v) => !v);
              clearSelection();
              setSelectionMode(false);
            }}
          >
            <Star size={16} weight={showFavorites ? "fill" : "regular"} /> Favorites
          </Button>
          <Button
            variant={selectionMode ? "primary" : "secondary"}
            onClick={toggleSelectionMode}
            aria-pressed={selectionMode}
          >
            {selectionMode ? <CheckSquare size={16} /> : <SquaresFour size={16} />}
            {selectionMode ? "Done" : "Select"}
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              setNewFolder({ id: `new-${Date.now()}`, name: "new_folder" })
            }
          >
            <Plus size={16} /> New folder
          </Button>
          <Button variant="secondary" onClick={() => setUploadOpen(true)}>
            <UploadSimple size={16} /> Upload
          </Button>
        </div>
      </div>

      {/* Selection toolbar */}
      {selectionMode && selectedCount > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-primary/40 bg-primary-container/20 px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-medium text-on-background">
            <CheckSquare size={16} className="text-primary" />
            {selectedCount} selected
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={commonSourceFormat === null}
              title={
                commonSourceFormat === null
                  ? "Select files of the same type to convert"
                  : `Convert ${selectedFiles.length} ${selectedFiles.length === 1 ? "file" : "files"} (${commonSourceFormat.toUpperCase()})`
              }
              onClick={() => setMassConvertOpen(true)}
            >
              <ArrowsClockwise size={15} /> Convert
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={confirmBatchDelete}
            >
              <Trash size={15} /> Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={clearSelection}>
              <X size={15} /> Clear
            </Button>
          </div>
        </div>
      )}

      {/* Breadcrumb path */}
      <div className="flex flex-wrap items-center gap-1 text-sm" aria-label="Folder path">
        <BreadcrumbDrop
          target={null}
          onMove={handleMove}
          onMoveFolder={handleMoveFolder}
          draggedFileId={draggedFileId}
          draggedFolderId={draggedFolderId}
          className="inline-flex rounded-md"
          overClassName="bg-surface-variant ring-1 ring-primary/30"
        >
          <button
            onClick={() => {
              if (showFavorites) return;
              setPath([]);
            }}
            className={`inline-flex items-center gap-1 rounded-md px-2 py-1 transition-colors ${
              showFavorites || path.length === 0
                ? "text-on-background"
                : "text-muted hover:bg-surface-variant hover:text-on-background"
            }`}
          >
            <House size={15} /> My Drive
          </button>
        </BreadcrumbDrop>
        {path.map((folder, i) => (
          <motion.span
            key={folder.id}
            className="inline-flex items-center"
            initial={reduce ? false : { opacity: 0, x: -6 }}
            animate={reduce ? undefined : { opacity: 1, x: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <CaretRight size={13} className="text-muted" />
            <BreadcrumbDrop
              target={folder.id}
              onMove={handleMove}
              onMoveFolder={handleMoveFolder}
              draggedFileId={draggedFileId}
              draggedFolderId={draggedFolderId}
              className="inline-flex rounded-md"
              overClassName="bg-surface-variant ring-1 ring-primary/30"
            >
              <button
                onClick={() => goTo(i)}
                className={`rounded-md px-2 py-1 transition-colors ${
                  i === path.length - 1
                    ? "font-medium text-on-background"
                    : "text-muted hover:bg-surface-variant hover:text-on-background"
                }`}
              >
                {folder.name}
              </button>
            </BreadcrumbDrop>
          </motion.span>
        ))}

        {/* Favorites-mode indicator + clear-filter control */}
        {showFavorites && (
          <motion.span
            className="inline-flex items-center gap-1.5"
            initial={reduce ? false : { opacity: 0, x: -6 }}
            animate={reduce ? undefined : { opacity: 1, x: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <CaretRight size={13} className="text-muted" />
            <span className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-primary">
              <Star size={14} weight="fill" className="text-warning" /> Favorites
            </span>
            <button
              type="button"
              onClick={() => {
                setShowFavorites(false);
                clearSelection();
                setSelectionMode(false);
              }}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
            >
              <X size={12} /> Clear filter
            </button>
          </motion.span>
        )}
      </div>

      <AnimatePresence mode="wait">
        {loading ? (
          <motion.div
            key="skeleton"
            className="grid grid-cols-[repeat(auto-fill,12.5rem)] gap-4"
            initial={reduce ? false : { opacity: 0 }}
            animate={reduce ? undefined : { opacity: 1 }}
            exit={reduce ? undefined : { opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="w-full overflow-hidden rounded-xl border border-outline bg-surface">
                <Skeleton className="aspect-square w-full rounded-none" />
                <div className="p-3">
                  <Skeleton className="h-4 w-3/4" />
                </div>
              </div>
            ))}
          </motion.div>
        ) : (
          <motion.div
            key="content"
            className="space-y-6"
            initial={reduce ? false : { opacity: 0 }}
            animate={reduce ? undefined : { opacity: 1 }}
            exit={reduce ? undefined : { opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            {/* Folders section — horizontal rows stacked in a grid, always on top. */}
            {(folders.length > 0 || newFolder) && (
              <motion.div
                className="grid grid-cols-3 gap-4"
                variants={reduce ? undefined : staggerContainer}
                initial={reduce ? false : "hidden"}
                animate={reduce ? undefined : "visible"}
                {...makeDropHandlers(currentFolderId)}
              >
                {/* New-folder placeholder card (inline editable) */}
                {newFolder && (
                  <Item className="w-full">
                    <div className="flex w-full items-center gap-3 rounded-xl border border-primary/50 bg-surface px-4 py-4">
                      <FolderSimple size={22} weight="duotone" className="shrink-0 text-primary" />
                      <form onSubmit={saveNewFolder} className="flex min-w-0 flex-1 items-center gap-1.5">
                        <input
                          ref={newFolderInput}
                          value={newFolder.name}
                          onChange={(e) => setNewFolder({ ...newFolder, name: e.target.value })}
                          onFocus={(e) => e.target.select()}
                          className="min-w-0 flex-1 rounded border border-outline-strong bg-surface-variant px-2 py-1 text-sm text-on-background focus:border-primary focus:outline-none"
                          aria-label="Folder name"
                        />
                        <button type="submit" className="text-primary hover:text-primary/80" aria-label="Save folder">
                          <Check size={16} weight="bold" />
                        </button>
                        <button type="button" onClick={() => setNewFolder(null)} className="text-muted hover:text-error" aria-label="Cancel">
                          <X size={16} />
                        </button>
                      </form>
                    </div>
                  </Item>
                )}
                {folders.map((folder) => (
                  <Item key={folder.id} className="w-full">
                    <FolderCard
                      folder={folder}
                      selectionMode={selectionMode}
                      selected={selectedFolderIds.has(folder.id)}
                      onToggleSelect={() => toggleFolder(folder.id, false)}
                      isDragging={draggedFolderId === folder.id}
                      onDragStartFolder={(id) => setDraggedFolderId(id)}
                      onDragEnd={() => setDraggedFolderId(null)}
                      onOpen={() => openFolder(folder)}
                      onRename={(name) => renameFolder(folder, name)}
                      onDelete={() => deleteFolder(folder)}
                      onMove={() => setMoveTarget({ kind: "folder", id: folder.id, name: folder.name, currentParentId: folder.parent_id })}
                      onDropFile={(fileId) => void handleMove(fileId, folder.id)}
                      onDropFolder={(folderId) => void handleMoveFolder(folderId, folder.id)}
                      draggedFileId={draggedFileId}
                      draggedFolderId={draggedFolderId}
                    />
                  </Item>
                ))}
              </motion.div>
            )}

            {/* Files section — smaller card grid below the folders. */}
            {files.length > 0 && (
              <motion.div
                className="grid grid-cols-[repeat(auto-fill,12.5rem)] gap-4"
                variants={reduce ? undefined : staggerContainer}
                initial={reduce ? false : "hidden"}
                animate={reduce ? undefined : "visible"}
              >
                {files.map((file) => (
                  <Item key={file.id} className="w-full">
                    <FileCard
                      file={file}
                      selectionMode={selectionMode}
                      selected={selectedFileIds.has(file.id)}
                      onToggleSelect={() => toggleFile(file.id, false)}
                      onDownload={() => downloadFile(file)}
                      onConvert={() => setConvertFile(file)}
                      onRename={(name) => renameFile(file.id, name)}
                      onDelete={() => deleteFile(file.id)}
                      onToggleFavorite={() => toggleFavorite(file)}
                      onMove={() => setMoveTarget({ kind: "file", id: file.id, name: file.file_name, currentParentId: file.folder_id })}
                      onDragStart={(id) => setDraggedFileId(id)}
                      onDragEnd={() => setDraggedFileId(null)}
                      isDragging={draggedFileId === file.id}
                    />
                  </Item>
                ))}
              </motion.div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty state — fades/scale-in gently once loaded. */}
      {!loading && folders.length === 0 && files.length === 0 && !newFolder && (
        <PopIn className="rounded-xl border border-outline bg-surface py-16 text-center">
          <p className="font-display text-lg font-semibold">
            {showFavorites ? "No favorites yet" : "This folder is empty"}
          </p>
          <p className="mt-1 text-sm text-muted">
            {showFavorites
              ? "Star a file to see it here."
              : "Add a file or create a folder to get started."}
          </p>
        </PopIn>
      )}

      {/* Upload-to-library modal */}
      <FilesUploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        folderId={currentFolderId}
        onUploaded={load}
      />

      {/* Convert-an-existing-file modal */}
      <FilesConvertModal
        open={convertFile !== null}
        onClose={() => setConvertFile(null)}
        file={convertFile}
      />

      {/* Mass-convert selected files modal */}
      <FilesMassConvertModal
        open={massConvertOpen}
        onClose={() => setMassConvertOpen(false)}
        files={selectedFiles}
        sourceFormat={commonSourceFormat ?? ""}
        onConverted={runMassConvert}
      />

      {/* "Move to…" modal (files + folders) */}
      <FilesMoveModal
        open={moveTarget !== null}
        onClose={() => setMoveTarget(null)}
        item={moveTarget}
        onMove={handleMoveTo}
      />

      {/* Styled delete confirmation */}
      <DeleteConfirmModal
        state={deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
      />
    </div>
  );
}

/** Accessible confirmation dialog for destructive actions. */
function DeleteConfirmModal({
  state,
  onClose,
}: {
  state: { title: string; message: string; onConfirm: () => Promise<void> | void } | null;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={state !== null}
      onClose={() => { if (!busy) onClose(); }}
      title={state?.title ?? ""}
      description={state?.message}
      maxWidth="max-w-sm"
    >
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          disabled={busy}
          onClick={async () => {
            if (!state) return;
            setBusy(true);
            try {
              await state.onConfirm();
              onClose();
            } catch {
              /* errors already surfaced by the handlers */
            } finally {
              setBusy(false);
            }
          }}
        >
          <Trash size={16} /> {busy ? "Deleting…" : "Delete"}
        </Button>
      </div>
    </Modal>
  );
}

/** Drop target wrapping a breadcrumb item so a file or folder can be moved onto any ancestor. */
function BreadcrumbDrop({
  target,
  onMove,
  onMoveFolder,
  draggedFileId,
  draggedFolderId,
  className,
  overClassName,
  children,
}: {
  target: string | null;
  onMove: (fileId: string, target: string | null) => void;
  onMoveFolder: (folderId: string, target: string | null) => void;
  draggedFileId: string | null;
  draggedFolderId: string | null;
  className: string;
  overClassName: string;
  children: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const overCount = useRef(0);
  const active = draggedFileId !== null || draggedFolderId !== null;

  return (
    <div
      className={`${className} ${over ? overClassName : ""}`}
      onDragOver={(e) => {
        if (!active) return;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = "move";
      }}
      onDragEnter={(e) => {
        if (!active) return;
        e.preventDefault();
        e.stopPropagation();
        overCount.current += 1;
        setOver(true);
      }}
      onDragLeave={() => {
        if (!active) return;
        overCount.current -= 1;
        if (overCount.current <= 0) {
          overCount.current = 0;
          setOver(false);
        }
      }}
      onDrop={(e) => {
        if (!active) return;
        e.preventDefault();
        e.stopPropagation();
        overCount.current = 0;
        setOver(false);
        const fileId = e.dataTransfer.getData(MOVE_MIME);
        const folderId = e.dataTransfer.getData(MOVE_FOLDER_MIME);
        if (fileId) onMove(fileId, target);
        else if (folderId) onMoveFolder(folderId, target);
      }}
    >
      {children}
    </div>
  );
}

function FolderCard({
  folder,
  selectionMode,
  selected,
  onToggleSelect,
  isDragging,
  onDragStartFolder,
  onDragEnd,
  onOpen,
  onRename,
  onDelete,
  onMove,
  onDropFile,
  onDropFolder,
  draggedFileId,
  draggedFolderId,
}: {
  folder: FolderResponse;
  selectionMode: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  isDragging: boolean;
  onDragStartFolder: (id: string) => void;
  onDragEnd: () => void;
  onOpen: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  onMove: () => void;
  onDropFile: (fileId: string) => void;
  onDropFolder: (folderId: string) => void;
  draggedFileId: string | null;
  draggedFolderId: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(folder.name);
  const [over, setOver] = useState(false);
  const overCount = useRef(0);
  const active = draggedFileId !== null || draggedFolderId !== null;
  // Whether the dragged folder (if any) is THIS folder — dropping onto itself
  // is a no-op that we want to visually suppress.
  const draggingSelf = draggedFolderId === folder.id;

  function submit() {
    onRename(draft);
    setEditing(false);
  }

  function handleDragStart(e: DragEvent<HTMLDivElement>) {
    e.dataTransfer.setData(MOVE_FOLDER_MIME, folder.id);
    e.dataTransfer.setData("text/plain", folder.name);
    e.dataTransfer.effectAllowed = "move";
    onDragStartFolder(folder.id);
  }

  const selectable = selectionMode && !editing;
  const reduce = useReducedMotion();
  // `motion` is layered on an INNER element; the root stays a plain <div> so
  // HTML5 drag events (onDragStart/onDragEnd/onDragOver/onDrop) are never
  // intercepted by motion's pan-gesture handlers.
  return (
    <div
      draggable={selectionMode ? false : !editing}
      onDragStart={handleDragStart}
      onDragEnd={onDragEnd}
      onClick={selectable ? onToggleSelect : undefined}
      onDragOver={(e) => {
        if (!active || draggingSelf) return;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = "move";
      }}
      onDragEnter={(e) => {
        if (!active || draggingSelf) return;
        const t = e.dataTransfer.types;
        if (!t.includes(MOVE_MIME) && !t.includes(MOVE_FOLDER_MIME)) return;
        e.preventDefault();
        e.stopPropagation();
        overCount.current += 1;
        setOver(true);
      }}
      onDragLeave={() => {
        if (!active) return;
        overCount.current -= 1;
        if (overCount.current <= 0) {
          overCount.current = 0;
          setOver(false);
        }
      }}
      onDrop={(e) => {
        if (!active || draggingSelf) return;
        e.preventDefault();
        e.stopPropagation();
        overCount.current = 0;
        setOver(false);
        const fileId = e.dataTransfer.getData(MOVE_MIME);
        const folderId = e.dataTransfer.getData(MOVE_FOLDER_MIME);
        if (fileId) onDropFile(fileId);
        else if (folderId) onDropFolder(folderId);
      }}
    >
      <motion.div
        whileHover={reduce || editing || isDragging ? undefined : { y: -3, scale: 1.01 }}
        whileTap={reduce || editing || isDragging ? undefined : { scale: 0.98 }}
        animate={reduce || editing ? undefined : { scale: selected ? 1.02 : 1 }}
        transition={{ type: "spring", stiffness: 300, damping: 26 }}
        className={`group flex w-full items-center gap-3 rounded-xl border px-4 py-4 transition-colors ${
          selected
            ? "border-primary bg-primary-container/20 ring-2 ring-primary/40"
            : over
              ? "border-primary bg-primary-container/20 ring-2 ring-primary/30"
              : "border-outline bg-surface hover:border-primary/50"
        } ${isDragging ? "opacity-40" : ""}`}
      >
        {selectable && (
          <SelectionCheckbox selected={selected} ariaLabel={`Select folder ${folder.name}`} />
        )}
        <FolderSimple
          size={22}
          weight="duotone"
          className={`shrink-0 ${selectable ? "" : "cursor-pointer"} text-warning ${
            selectable ? "" : ""
          }`}
          onClick={selectable ? undefined : onOpen}
          aria-label={`Open folder ${folder.name}`}
        />

        {editing ? (
          <form
            className="flex min-w-0 flex-1 items-center gap-1.5"
            onSubmit={(e) => { e.preventDefault(); submit(); }}
          >
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onFocus={(e) => e.target.select()}
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
                if (e.key === "Escape") { setEditing(false); setDraft(folder.name); }
              }}
              className="min-w-0 flex-1 rounded border border-outline-strong bg-surface-variant px-2 py-1 text-sm text-on-background focus:border-primary focus:outline-none"
              aria-label="Folder name"
            />
            <button type="submit" className="text-primary" aria-label="Save">
              <Check size={16} weight="bold" />
            </button>
            <button type="button" onClick={() => { setEditing(false); setDraft(folder.name); }} className="text-muted hover:text-error" aria-label="Cancel">
              <X size={16} />
            </button>
          </form>
        ) : (
          <>
            <button
              type="button"
              onClick={selectable ? onToggleSelect : onOpen}
              className="min-w-0 flex-1 truncate text-left text-sm font-medium text-on-background"
            >
              {folder.name}
            </button>
            {!selectable && (
              <CardMenu
                onRename={() => { setDraft(folder.name); setEditing(true); }}
                onDelete={onDelete}
                onMove={onMove}
              />
            )}
          </>
        )}
      </motion.div>
    </div>
  );
}

function FileCard({
  file,
  selectionMode,
  selected,
  onToggleSelect,
  onDownload,
  onConvert,
  onRename,
  onDelete,
  onToggleFavorite,
  onMove,
  onDragStart,
  onDragEnd,
  isDragging,
}: {
  file: FileMetadataResponse;
  selectionMode: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onDownload: () => void;
  onConvert: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  onToggleFavorite: () => void;
  onMove: () => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  isDragging: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(file.file_name);
  const isFavorite = file.is_favorite ?? false;

  function handleDragStart(e: DragEvent<HTMLDivElement>) {
    e.dataTransfer.setData(MOVE_MIME, file.id);
    e.dataTransfer.setData("text/plain", file.file_name);
    e.dataTransfer.effectAllowed = "move";
    onDragStart(file.id);
  }

  function submit() {
    onRename(draft);
    setEditing(false);
  }

  // Whole-card selectable area replaces the old filename-only click target in
  // selection mode. Dragging is disabled while selectable so it never conflicts
  // with click-to-select (mirrors FolderCard).
  const selectable = selectionMode && !editing;

  // NOTE: no `overflow-hidden` on the root — the absolutely-positioned CardMenu
  // dropdown (absolute right-0 top-8 z-20) must be able to overflow the card.
  // The rounded clip lives only on the thumbnail wrapper below.
  const reduce = useReducedMotion();
  // `motion` is layered on an INNER element; the root stays a plain <div> so
  // HTML5 drag events (onDragStart/onDragEnd) are never intercepted by motion's
  // pan-gesture handlers.
  return (
    <div
      draggable={selectable ? false : !editing}
      onDragStart={handleDragStart}
      onDragEnd={onDragEnd}
      onClick={selectable ? onToggleSelect : undefined}
      onKeyDown={selectable ? (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggleSelect();
        }
      } : undefined}
      role={selectable ? "button" : undefined}
      tabIndex={selectable ? 0 : -1}
    >
      <motion.div
        whileHover={reduce || editing || isDragging ? undefined : { y: -3, scale: 1.01 }}
        whileTap={reduce || editing || isDragging ? undefined : { scale: 0.98 }}
        animate={reduce || editing ? undefined : { scale: selected ? 1.02 : 1 }}
        transition={{ type: "spring", stiffness: 300, damping: 26 }}
        className={`group flex w-full flex-col rounded-xl border transition-opacity ${
          editing
            ? "border-outline bg-surface"
            : selected
              ? "cursor-default border-primary bg-primary-container/20 ring-2 ring-primary/40"
              : selectable
                ? "cursor-default border-outline bg-surface hover:border-primary/50"
                : `cursor-grab border-outline bg-surface hover:border-primary/50 active:cursor-grabbing ${isDragging ? "opacity-40" : ""}`
        }`}
      >
        {/* Whole card is ~0.8cm smaller than the folder rows: slightly shorter
            thumbnail and tighter padding. */}
        <div className="px-2 pt-2">
          <div className="overflow-hidden rounded-lg">
            <FileThumbnail
              fileName={file.file_name}
              mimeType={file.mime_type}
              url={null}
              className="aspect-[4/3]"
            />
          </div>
        </div>
        <div className="flex items-center gap-1 p-2.5">
          {editing ? (
            <form
              className="flex min-w-0 flex-1 items-center gap-1"
              onSubmit={(e) => { e.preventDefault(); submit(); }}
            >
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onFocus={(e) => e.target.select()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submit();
                  if (e.key === "Escape") { setEditing(false); setDraft(file.file_name); }
                }}
                className="min-w-0 flex-1 rounded border border-outline-strong bg-surface-variant px-1.5 py-1 text-sm text-on-background focus:border-primary focus:outline-none"
                aria-label="File name"
              />
              <button type="submit" className="text-primary" aria-label="Save">
                <Check size={16} weight="bold" />
              </button>
              <button type="button" onClick={() => { setEditing(false); setDraft(file.file_name); }} className="text-muted hover:text-error" aria-label="Cancel">
                <X size={16} />
              </button>
            </form>
          ) : (
            <>
              {selectionMode && (
                <SelectionCheckbox
                  selected={selected}
                  ariaLabel={`Select file ${file.file_name}`}
                />
              )}
              <div
                className="min-w-0 flex-1 truncate text-left text-sm font-medium text-on-background"
                title={file.file_name}
              >
                {file.file_name}
              </div>
              {!selectable && (
                <button
                  type="button"
                  onClick={onToggleFavorite}
                  aria-label={isFavorite ? "Remove from favorites" : "Add to favorites"}
                  aria-pressed={isFavorite}
                  title={isFavorite ? "Remove from favorites" : "Add to favorites"}
                  className="shrink-0 rounded p-1 text-muted transition-colors hover:bg-surface-variant hover:text-primary"
                >
                  <Star size={16} weight={isFavorite ? "fill" : "regular"} className={isFavorite ? "text-warning" : ""} />
                </button>
              )}
              {!selectable && (
                <CardMenu
                  onDownload={onDownload}
                  onConvert={onConvert}
                  onRename={() => { setDraft(file.file_name); setEditing(true); }}
                  onMove={onMove}
                  onToggleFavorite={onToggleFavorite}
                  isFavorite={isFavorite}
                  onDelete={onDelete}
                />
              )}
            </>
          )}
        </div>
      </motion.div>
    </div>
  );
}

function CardMenu({
  onRename,
  onDownload,
  onConvert,
  onMove,
  onToggleFavorite,
  isFavorite,
  onDelete,
}: {
  onRename?: () => void;
  onDownload?: () => void;
  onConvert?: () => void;
  onMove?: () => void;
  onToggleFavorite?: () => void;
  isFavorite?: boolean;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative" draggable={false}>
      <button
        type="button"
        draggable={false}
        onDragStart={(e) => e.stopPropagation()}
        onClick={() => setOpen((v) => !v)}
        aria-label="More options"
        aria-expanded={open}
        className="rounded p-1 text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
      >
        <DotsThreeVertical size={18} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-8 z-20 w-44 overflow-hidden rounded-lg border border-outline bg-surface p-1 shadow-xl">
            {onMove && (
              <button
                onClick={() => { setOpen(false); onMove(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <FolderSimple size={15} /> Move to…
              </button>
            )}
            {onDownload && (
              <button
                onClick={() => { setOpen(false); onDownload(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <Download size={15} /> Download
              </button>
            )}
            {onConvert && (
              <button
                onClick={() => { setOpen(false); onConvert(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <ArrowsClockwise size={15} /> Convert
              </button>
            )}
            {onToggleFavorite && (
              <button
                onClick={() => { setOpen(false); onToggleFavorite(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <Star size={15} weight={isFavorite ? "fill" : "regular"} className={isFavorite ? "text-warning" : ""} />
                {isFavorite ? "Remove from favorites" : "Add to favorites"}
              </button>
            )}
            {onRename && (
              <button
                onClick={() => { setOpen(false); onRename(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <PenNib size={15} /> Rename
              </button>
            )}
            <button
              onClick={() => { setOpen(false); onDelete(); }}
              className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-error hover:bg-error/10"
            >
              <Trash size={15} /> Delete
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/** A small circular check that indicates a card is selected in selection mode. */
function SelectionCheckbox({
  selected,
  ariaLabel,
}: {
  selected: boolean;
  ariaLabel: string;
}) {
  return (
    <span
      aria-hidden="false"
      aria-label={ariaLabel}
      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
        selected
          ? "border-primary bg-primary text-on-primary"
          : "border-outline-strong bg-surface text-transparent"
      }`}
    >
      <Check size={13} weight="bold" className={selected ? "opacity-100" : "opacity-0"} />
    </span>
  );
}

