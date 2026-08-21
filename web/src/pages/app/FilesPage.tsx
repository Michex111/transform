import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { FolderOpen, DotsThreeOutline, UploadSimple, Plus, Download, Trash } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, FormatChip } from "@/components/ui";
import { formatBytes, formatDate } from "@/lib/format";
import type { FolderResponse, FileMetadataResponse } from "@/api/types";

export function FilesPage() {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const [folders, setFolders] = useState<FolderResponse[]>([]);
  const [files, setFiles] = useState<FileMetadataResponse[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([client.listFolders(), client.listFiles()])
      .then(([f, fl]) => {
        if (!active) return;
        setFolders(f.folders);
        setFiles(fl.files);
      })
      .catch((e: Error) => error(e.message));
    return () => {
      active = false;
    };
  }, [client, error]);

  async function createFolder(e: FormEvent) {
    e.preventDefault();
    try {
      const f = await client.createFolder(name);
      setFolders((prev) => [...prev, f]);
      setCreating(false);
      setName("");
      success("Folder created");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not create folder");
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

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">Files</h1>
          <p className="text-sm text-muted">Your library, in folders.</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setCreating((v) => !v)}>
            <Plus size={16} /> New folder
          </Button>
          <Link to="/app/convert">
            <Button variant="secondary">
              <UploadSimple size={16} /> Upload
            </Button>
          </Link>
        </div>
      </div>

      {creating && (
        <form onSubmit={createFolder} className="flex items-center gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Folder name"
            className="h-10 flex-1 rounded-lg border border-outline-strong bg-surface-variant px-3 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none"
            autoFocus
          />
          <Button type="submit" size="sm">
            Create
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setCreating(false)}>
            Cancel
          </Button>
        </form>
      )}

      <Card className="overflow-hidden">
        {folders.length > 0 && (
          <>
            <div className="border-b border-outline px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted">
              Folders
            </div>
            <ul className="divide-y divide-outline">
              {folders.map((f) => (
                <li key={f.id} className="flex items-center gap-3 px-5 py-3 hover:bg-surface-variant/50">
                  <FolderOpen size={20} className="text-warning" />
                  <span className="text-sm text-on-background">{f.name}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        <div className="border-b border-outline px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted">
          Files
        </div>
        {files.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <p className="font-display text-lg font-semibold">Add your first file</p>
            <p className="mt-1 text-sm text-muted">Upload a file to start building your library.</p>
          </div>
        ) : (
          <ul className="divide-y divide-outline">
            {files.map((file) => (
              <li key={file.id} className="flex items-center gap-3 px-5 py-3 hover:bg-surface-variant/50">
                <FormatChip format={file.mime_type.split("/").pop() ?? "txt"} />
                <span className="min-w-0 flex-1 truncate text-sm text-on-background">{file.file_name}</span>
                <span className="hidden font-mono text-xs text-muted sm:block">{formatBytes(file.file_size_bytes)}</span>
                <span className="hidden font-mono text-xs text-muted md:block">{formatDate(file.created_at)}</span>
                <div className="flex items-center gap-2">
                  <a
                    href="#"
                    onClick={(e) => e.preventDefault()}
                    className="text-muted hover:text-primary"
                    aria-label="Download"
                  >
                    <Download size={18} />
                  </a>
                  <button
                    onClick={() => deleteFile(file.id)}
                    className="text-muted hover:text-error"
                    aria-label="Delete"
                  >
                    <Trash size={18} />
                  </button>
                  <DotsThreeOutline size={18} className="text-muted" />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
