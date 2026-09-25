import { useEffect, useState } from "react";
import { FolderSimple } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { FolderPickerModal } from "@/components/FolderPickerModal";
import { ROOT_FOLDER_LABEL, defaultSaveFolderId } from "@/lib/saveToDrive";

/**
 * The "Default save location" card: the folder a "Save to Drive" lands in.
 *
 * Standalone beside `ProfileSection`/`PasswordSection` rather than a settings
 * tab of its own — it is one preference with two controls, which does not
 * warrant a tab and would change the tab list the settings tests pin.
 *
 * The stored value is an id, and an id is not something to show a person, so
 * the name is resolved on load. That request is also the only way to learn that
 * a stored folder has since been deleted, so its failure is handled as a normal
 * state (fall back to the root, explain, offer to pick again) rather than as an
 * error — merely opening this page must never fire an error toast.
 */
export function DriveSection() {
  const { api: client, user, setUser } = useAuth();
  const { success, error } = useToast();
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  // The id whose name has actually been resolved, kept WITH the id it belongs
  // to. A plain `resolving` flag meant the first paint showed a confident
  // "My Drive" (the effect had not run yet), then a skeleton, then the real
  // name — a visible flash of the wrong value. Comparing ids makes "no
  // preference" and "a preference we have not named yet" different states
  // rather than the same one, with no render-time flag to keep in sync.
  const [resolved, setResolved] = useState<{
    id: string;
    name: string | null;
    missing: boolean;
  } | null>(null);

  const configuredId = defaultSaveFolderId(user);
  const settled = resolved?.id === configuredId ? resolved : null;
  const pendingName = configuredId !== null && settled === null;

  useEffect(() => {
    if (configuredId === null) {
      // No preference: nothing to resolve, and the card reads "My Drive".
      setResolved(null);
      return;
    }
    let active = true;
    client
      .getFolderContents(configuredId)
      .then((contents) => {
        if (!active) return;
        const name = contents.folder.name?.trim() || null;
        // A folder that resolved but carries no name is as unusable as one that
        // is gone: either way the card cannot truthfully print a name.
        setResolved({ id: configuredId, name, missing: name === null });
      })
      .catch(() => {
        if (!active) return;
        // Deleted, or not this account's folder. Not an error for the user —
        // the preference is simply stale, and the file still has somewhere to
        // go (the root).
        setResolved({ id: configuredId, name: null, missing: true });
      });
    return () => {
      active = false;
    };
  }, [configuredId, client]);

  /**
   * Write the preference and publish the server's record.
   *
   * The response — not the picked value — is what the card then shows: the PATCH
   * is the only thing that knows whether the folder was accepted (an unknown or
   * foreign id answers 404 `Folder not found`), and assuming success would leave
   * a stored folder on screen that the server never agreed to.
   */
  async function saveDefault(folderId: string | null) {
    setSaving(true);
    try {
      setUser(await client.updateProfile({ default_save_folder_id: folderId }));
      success(
        folderId
          ? "Default save location updated"
          : `Default save location reset to ${ROOT_FOLDER_LABEL}`,
      );
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not update your default save location.");
    } finally {
      setSaving(false);
    }
  }

  const showName = settled !== null && !settled.missing && settled.name !== null;

  return (
    <>
      <Card className="p-6">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">Default save location</h2>
          <FolderSimple size={20} className="text-muted" aria-hidden="true" />
        </div>
        <p className="mb-4 text-sm text-muted">
          Where “Save to Drive” files your completed conversions. You can still choose a different
          folder for a single file.
        </p>

        <div className="rounded-lg border border-outline bg-surface-variant/40 px-3 py-2.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted">Saving to</p>
          {pendingName ? (
            // Same placeholder treatment as the other sections use while they
            // resolve something the user asked to see.
            <Skeleton className="mt-1.5 h-5 w-32" />
          ) : (
            <p className="mt-0.5 truncate text-sm font-semibold text-on-background">
              {showName ? settled.name : ROOT_FOLDER_LABEL}
            </p>
          )}
          {settled?.missing && (
            <p className="mt-1 text-xs text-muted">
              The folder you chose no longer exists, so files are saved to {ROOT_FOLDER_LABEL} until
              you pick a new one.
            </p>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => setPicking(true)}
            disabled={saving}
            className="pointer-coarse:min-h-11"
          >
            {saving ? "Saving…" : "Change…"}
          </Button>
          {/* Only offered when there is something to reset, so the card never
              presents a control that would be a no-op. */}
          {configuredId !== null && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => saveDefault(null)}
              disabled={saving}
              className="pointer-coarse:min-h-11"
            >
              Reset to {ROOT_FOLDER_LABEL}
            </Button>
          )}
        </div>
      </Card>

      <FolderPickerModal
        open={picking}
        onClose={() => setPicking(false)}
        title="Choose a default folder"
        confirmLabel="Use this folder"
        hint="Completed conversions you save to your drive will land in this folder by default."
        onConfirm={(folderId) => saveDefault(folderId)}
      />
    </>
  );
}
