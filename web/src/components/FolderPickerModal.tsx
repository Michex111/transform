import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  CaretRight,
  CircleNotch,
  FolderOpen,
  FolderSimple,
  House,
  WarningCircle,
} from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { Modal } from "@/components/Modal";
import { Button, Skeleton } from "@/components/ui";
import { folderLabel } from "@/lib/saveToDrive";
import type { FolderResponse } from "@/api/types";

/**
 * "Choose a folder" — a Drive-style browser the user confirms a destination
 * with.
 *
 * The destination is the folder currently being browsed (the root, "My Drive",
 * when the trail is empty), exactly like `FilesMoveModal`. It is a separate
 * component rather than a mode of that one on purpose: the move dialog forbids
 * targeting the item's own subtree and disables unchanged destinations, which
 * are meaningless when the thing being placed is a copy that does not exist in
 * the drive yet, and its callers depend on those rules.
 *
 * The dialog can never dead-end: an empty folder is a valid destination ("save
 * here"), the confirm button is always available while not busy, and a folder
 * that will not load still leaves the current location selectable with an
 * explanation instead of a spinner that never resolves.
 */
export function FolderPickerModal({
  open,
  onClose,
  onConfirm,
  hint,
  confirmLabel = "Save here",
  allowSetDefault = false,
  defaultChecked = false,
  title = "Choose a folder",
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (folderId: string | null, useAsDefault: boolean) => Promise<void> | void;
  hint?: string;
  confirmLabel?: string;
  /** Renders a "Use as my default folder" checkbox (the Settings page does not need it). */
  allowSetDefault?: boolean;
  defaultChecked?: boolean;
  title?: string;
}) {
  const { api: client } = useAuth();
  // The browsing trail inside the dialog; empty means the root ("My Drive").
  const [navPath, setNavPath] = useState<FolderResponse[]>([]);
  const [subfolders, setSubfolders] = useState<FolderResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [useAsDefault, setUseAsDefault] = useState(defaultChecked);

  const current = navPath[navPath.length - 1] ?? null;
  const currentId = current?.id ?? null;

  // Reset the browser and the checkbox each time the dialog OPENS.
  //
  // Keyed on the transition rather than on `open` alone: the effect depends on
  // `defaultChecked` too, and without the guard a parent re-render that changed
  // that prop while the dialog was open would throw the user's navigation and
  // their checkbox away mid-choice.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open && !wasOpen.current) {
      setNavPath([]);
      setLoadFailed(false);
      setBusy(false);
      setUseAsDefault(defaultChecked);
    }
    wasOpen.current = open;
  }, [open, defaultChecked]);

  // Load the subfolders of the location being browsed. The root has no folder
  // object of its own, so it lists from the top level instead.
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setLoadFailed(false);
    const load = async () => {
      try {
        const result =
          currentId != null
            ? await client.getFolderContents(currentId)
            : await client.listFolders();
        if (!active) return;
        setSubfolders(result.folders);
      } catch {
        // The current location stays the destination, so a failed listing is
        // not a dead end — it just cannot be browsed any further.
        if (!active) return;
        setSubfolders([]);
        setLoadFailed(true);
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [open, currentId, client]);

  // Escape and the backdrop come through here, so a confirm already on its way
  // to the server cannot be dismissed out from under the request.
  function close() {
    if (busy) return;
    onClose();
  }

  async function confirm() {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm(currentId, allowSetDefault ? useAsDefault : false);
      onClose();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={close} title={title} maxWidth="max-w-md">
      <div className="space-y-4">
        {hint && <p className="text-sm text-muted">{hint}</p>}

        {/* Where the user currently is. Every crumb is a link back up the trail. */}
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
            <House size={14} /> {folderLabel(null)}
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

        {/* The folders inside the current location, to browse into. */}
        <div className="max-h-72 min-h-40 overflow-y-auto rounded-xl border border-outline">
          {loading ? (
            <div className="space-y-2 p-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : subfolders.length > 0 ? (
            <ul className="p-1.5">
              {subfolders.map((folder) => (
                <li key={folder.id}>
                  <button
                    type="button"
                    onClick={() => setNavPath((prev) => [...prev, folder])}
                    className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-surface-variant"
                  >
                    <FolderSimple size={18} weight="duotone" className="shrink-0 text-warning" />
                    <span className="min-w-0 flex-1 truncate text-on-background">{folder.name}</span>
                    <CaretRight size={13} className="shrink-0 text-muted" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-1.5 py-10 text-center">
              <FolderOpen size={26} className="text-muted" />
              <p className="text-sm text-muted">
                {loadFailed
                  ? "This folder couldn't be opened — you can still save it here."
                  : "No subfolders — you can save it here."}
              </p>
            </div>
          )}
        </div>

        {loadFailed && (
          <p className="flex items-start gap-1.5 text-xs text-error">
            <WarningCircle size={14} className="mt-0.5 shrink-0" aria-hidden />
            We couldn't list the folders here. Pick a destination above, or go back to{" "}
            {folderLabel(null)}.
          </p>
        )}

        {/* The destination is stated in words as well as highlighted in the
            trail: "Save here" alone does not say WHERE "here" is. */}
        <p className="text-sm text-muted">
          Destination:{" "}
          <span className="font-semibold text-on-background">{folderLabel(current)}</span>
        </p>

        {allowSetDefault && (
          <label className="flex items-center gap-2 text-sm text-on-background">
            <input
              type="checkbox"
              checked={useAsDefault}
              onChange={(e) => setUseAsDefault(e.target.checked)}
              className="h-4 w-4 rounded border-outline-strong accent-[var(--color-primary)]"
            />
            Also use this folder by default
          </label>
        )}

        {/* Footer. `Back` doubles as the cancel affordance at the root, like the
            move dialog, so the two browsers behave the same way. */}
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
            <ArrowLeft size={15} /> {navPath.length === 0 ? "Cancel" : "Back"}
          </button>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={confirm} disabled={busy}>
              {busy ? <CircleNotch size={16} className="animate-spin" /> : <FolderOpen size={16} />}
              {busy ? "Saving…" : confirmLabel}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
