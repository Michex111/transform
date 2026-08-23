import { useEffect, useMemo, useState } from "react";
import {
  FolderSimple,
  House,
  CaretRight,
  ArrowLeft,
  CircleNotch,
  FolderOpen,
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import type { FolderResponse } from "@/api/types";

export interface MoveItem {
  kind: "file" | "folder";
  id: string;
  name: string;
  /** The folder the item currently sits in (null = root). */
  currentParentId: string | null;
}

/**
 * "Move to…" modal — a Drive-style folder browser. The user navigates into a
 * destination folder (or stays at "My Drive"/root) and confirms. The destination
 * is the folder currently being viewed, matching the reference design.
 */
export function FilesMoveModal({
  open,
  onClose,
  item,
  onMove,
}: {
  open: boolean;
  onClose: () => void;
  item: MoveItem | null;
  onMove: (destFolderId: string | null) => Promise<void>;
}) {
  const { api: client } = useAuth();
  // Browsing trail inside the modal (empty = root/"My Drive").
  const [navPath, setNavPath] = useState<FolderResponse[]>([]);
  const [subfolders, setSubfolders] = useState<FolderResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const currentId = navPath[navPath.length - 1]?.id ?? null;

  // Reset the browser to root each time the modal opens.
  useEffect(() => {
    if (open) setNavPath([]);
  }, [open]);

  // Load the subfolders for the current browsing location.
  useEffect(() => {
    if (!open || !item) return;
    let active = true;
    setLoading(true);
    const load = async () => {
      try {
        const result =
          currentId != null
            ? await client.getFolderContents(currentId)
            : await client.listFolders();
        if (active) setSubfolders(result.folders);
      } catch {
        if (active) setSubfolders([]);
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [open, item, currentId, client]);

  // Destination = the folder currently being viewed (null = root).
  const destinationId = currentId;

  // A folder being moved must not target itself (moving into a descendant is
  // unreachable — you'd have to navigate through it first, which we block).
  const isSelf = item?.kind === "folder" && item.id === destinationId;
  const destDisabled = useMemo(
    () => Boolean(item && (destinationId === item.currentParentId || isSelf)),
    [item, destinationId, isSelf],
  );

  function reset() {
    setNavPath([]);
    setBusy(false);
  }

  function close() {
    if (busy) return;
    reset();
    onClose();
  }

  function enter(folder: FolderResponse) {
    // Block navigating INTO the folder currently being moved (avoids its subtree).
    if (item?.kind === "folder" && folder.id === item.id) return;
    setNavPath((prev) => [...prev, folder]);
  }

  async function confirm() {
    if (!item || destDisabled || busy) return;
    setBusy(true);
    try {
      await onMove(destinationId);
      reset();
      onClose();
    } finally {
      setBusy(false);
    }
  }

  // Whether a folder row can be entered (disabled if it's the item being moved).
  function rowDisabled(folder: FolderResponse) {
    return item?.kind === "folder" && folder.id === item.id;
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Move to…"
      description={item ? `${item.kind === "folder" ? "Folder" : "File"} — ${item.name}` : undefined}
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        {/* Current location breadcrumb chip */}
        <div className="flex items-center gap-1 overflow-x-auto rounded-lg border border-outline bg-surface-variant/50 px-3 py-2 text-sm">
          <button
            type="button"
            onClick={() => setNavPath([])}
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors ${
              navPath.length === 0
                ? "font-medium text-on-background"
                : "text-muted hover:bg-surface-variant hover:text-on-background"
            }`}
          >
            <House size={14} /> My Drive
          </button>
          {navPath.map((folder, i) => (
            <span key={folder.id} className="inline-flex items-center">
              <CaretRight size={12} className="text-muted" />
              <button
                type="button"
                onClick={() => setNavPath((prev) => prev.slice(0, i + 1))}
                className={`rounded-md px-1.5 py-0.5 transition-colors ${
                  i === navPath.length - 1
                    ? "font-medium text-on-background"
                    : "text-muted hover:bg-surface-variant hover:text-on-background"
                }`}
              >
                {folder.name}
              </button>
            </span>
          ))}
        </div>

        {/* Folder list to navigate into */}
        <div className="max-h-72 min-h-40 overflow-y-auto rounded-xl border border-outline">
          {loading ? (
            <div className="space-y-2 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : subfolders.length > 0 ? (
            <ul className="p-1.5">
              {subfolders.map((folder) => {
                const disabled = rowDisabled(folder);
                return (
                  <li key={folder.id}>
                    <button
                      type="button"
                      onClick={() => enter(folder)}
                      disabled={disabled}
                      aria-disabled={disabled}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 hover:bg-surface-variant disabled:hover:bg-transparent"
                    >
                      <FolderSimple size={18} weight="duotone" className="shrink-0 text-warning" />
                      <span className="min-w-0 flex-1 truncate text-on-background">{folder.name}</span>
                      <CaretRight size={13} className="shrink-0 text-muted" />
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-1.5 py-10 text-center">
              <FolderOpen size={26} className="text-muted" />
              <p className="text-sm text-muted">No subfolders — you can move it here.</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => {
              if (navPath.length === 0) close();
              else setNavPath((prev) => prev.slice(0, -1));
            }}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold text-muted transition-colors hover:bg-surface-variant hover:text-on-background disabled:opacity-50"
          >
            <ArrowLeft size={15} /> Back
          </button>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={confirm} disabled={destDisabled || busy}>
              {busy ? <CircleNotch size={16} className="animate-spin" /> : <FolderOpen size={16} />}
              {busy ? "Moving…" : "Move"}
            </Button>
          </div>
        </div>

        {destDisabled && (
          <p className="text-xs text-muted">
            {item?.kind === "folder" && isSelf
              ? "A folder can't be moved into itself."
              : "This item is already in the selected location."}
          </p>
        )}
      </div>
    </Modal>
  );
}
