import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
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
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Skeleton } from "@/components/ui";
import { FileThumbnail } from "@/components/FileThumbnail";
import { downloadFromUrl } from "@/lib/download";
import type { FolderResponse, FileMetadataResponse } from "@/api/types";

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

  const currentFolderId = path[path.length - 1]?.id ?? null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (currentFolderId) {
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
  }, [client, currentFolderId, error]);

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
      // Replace the placeholder card with the persisted folder.
      setFolders((prev) => prev.map((f) => (f.id === newFolder.id ? created : f)));
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
    if (!window.confirm(`Delete "${folder.name}" and everything inside it?`)) return;
    try {
      await client.deleteFolder(folder.id);
      setFolders((prev) => prev.filter((f) => f.id !== folder.id));
      success("Folder deleted");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not delete folder");
    }
  }

  async function deleteFile(id: string) {
    try {
      await client.deleteFile(id);
      setFiles((prev) => prev.filter((f) => f.id !== id));
      success("File deleted");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not delete file");
    }
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

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">My Drive</h1>
          <p className="text-sm text-muted">Your files, in folders.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() =>
              setNewFolder({ id: `new-${Date.now()}`, name: "new_folder" })
            }
          >
            <Plus size={16} /> New folder
          </Button>
          <Link to="/app/convert">
            <Button variant="secondary">
              <UploadSimple size={16} /> Upload
            </Button>
          </Link>
        </div>
      </div>

      {/* Breadcrumb path */}
      <div className="flex items-center gap-1 text-sm" aria-label="Folder path">
        <button
          onClick={() => setPath([])}
          className={`inline-flex items-center gap-1 rounded-md px-2 py-1 transition-colors ${
            path.length === 0
              ? "text-on-background"
              : "text-muted hover:bg-surface-variant hover:text-on-background"
          }`}
        >
          <House size={15} /> My Drive
        </button>
        {path.map((folder, i) => (
          <span key={folder.id} className="inline-flex items-center">
            <CaretRight size={13} className="text-muted" />
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
          </span>
        ))}
      </div>

      {loading ? (
        <div className="grid grid-cols-[repeat(auto-fill,12.5rem)] gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="w-full overflow-hidden rounded-xl border border-outline bg-surface">
              <Skeleton className="aspect-square w-full rounded-none" />
              <div className="p-3">
                <Skeleton className="h-4 w-3/4" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Folders section — horizontal rows stacked in a grid, always on top. */}
          {folders.length > 0 && (
            <div className="grid grid-cols-3 gap-4">
              {/* New-folder placeholder card (inline editable) */}
              {newFolder && (
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
              )}
              {folders.map((folder) => (
                <FolderCard
                  key={folder.id}
                  folder={folder}
                  onOpen={() => openFolder(folder)}
                  onRename={(name) => renameFolder(folder, name)}
                  onDelete={() => deleteFolder(folder)}
                />
              ))}
            </div>
          )}

          {/* Files section — smaller card grid below the folders. */}
          {files.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,12.5rem)] gap-4">
              {files.map((file) => (
                <FileCard
                  key={file.id}
                  file={file}
                  onDownload={() => downloadFile(file)}
                  onRename={(name) => renameFile(file.id, name)}
                  onDelete={() => deleteFile(file.id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {!loading && folders.length === 0 && files.length === 0 && !newFolder && (
        <div className="rounded-xl border border-outline bg-surface py-16 text-center">
          <p className="font-display text-lg font-semibold">This folder is empty</p>
          <p className="mt-1 text-sm text-muted">Add a file or create a folder to get started.</p>
        </div>
      )}
    </div>
  );
}

function FolderCard({
  folder,
  onOpen,
  onRename,
  onDelete,
}: {
  folder: FolderResponse;
  onOpen: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(folder.name);

  function submit() {
    onRename(draft);
    setEditing(false);
  }

  return (
    <div className="group flex w-full items-center gap-3 rounded-xl border border-outline bg-surface px-4 py-4 transition-colors hover:border-primary/50">
      <FolderSimple
        size={22}
        weight="duotone"
        className="shrink-0 text-warning"
        onClick={onOpen}
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
            onClick={onOpen}
            className="min-w-0 flex-1 truncate text-left text-sm font-medium text-on-background"
          >
            {folder.name}
          </button>
          <CardMenu
            onRename={() => { setDraft(folder.name); setEditing(true); }}
            onDelete={onDelete}
          />
        </>
      )}
    </div>
  );
}

function FileCard({
  file,
  onDownload,
  onRename,
  onDelete,
}: {
  file: FileMetadataResponse;
  onDownload: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(file.file_name);

  function submit() {
    onRename(draft);
    setEditing(false);
  }

  return (
    <div className="group flex w-full flex-col overflow-hidden rounded-xl border border-outline bg-surface transition-colors hover:border-primary/50">
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
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left text-sm font-medium text-on-background"
              title={file.file_name}
            >
              {file.file_name}
            </button>
            <CardMenu
              onDownload={onDownload}
              onRename={() => { setDraft(file.file_name); setEditing(true); }}
              onDelete={onDelete}
            />
          </>
        )}
      </div>
    </div>
  );
}

function CardMenu({
  onRename,
  onDownload,
  onDelete,
}: {
  onRename?: () => void;
  onDownload?: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
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
          <div className="absolute right-0 top-8 z-20 w-40 overflow-hidden rounded-lg border border-outline bg-surface p-1 shadow-xl">
            {onDownload && (
              <button
                onClick={() => { setOpen(false); onDownload(); }}
                className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-on-background hover:bg-surface-variant"
              >
                <Download size={15} /> Download
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

