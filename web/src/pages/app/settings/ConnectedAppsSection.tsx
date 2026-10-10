import { useEffect, useRef, useState } from "react";
import { PlugsConnected } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { ConnectedAppRow } from "@/pages/app/settings/ConnectedAppRow";
import { ConnectedAppPermissionsEditor } from "@/pages/app/settings/ConnectedAppPermissionsEditor";
import type {
  ConnectedAppResponse,
  McpFolderOption,
  UpdateConnectedAppRequest,
} from "@/api/types";
import {
  describeUpdateError,
  initialPermissionState,
  type ConnectedAppPermissionState,
} from "@/lib/connectedAppPermissions";

/**
 * The connected AI applications card.
 *
 * Distinct from the API keys card beside it, and deliberately so: an API key is
 * a long-lived credential the user pastes into their own code, whereas these
 * are third-party applications the user authorized through a browser consent
 * flow. They hold scoped, revocable tokens — which is what lets a user withdraw
 * one application's access without rotating anything else.
 *
 * `client_name` comes from the application, so it is rendered as text (React's
 * default) and never as markup.
 */
export function ConnectedAppsSection() {
  const { success, error } = useToast();
  const [apps, setApps] = useState<ConnectedAppResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [revokeTarget, setRevokeTarget] = useState<ConnectedAppResponse | null>(null);
  // The connection being edited, kept together with the selection in its dialog
  // so closing the dialog cannot leave a stale selection behind.
  const [editTarget, setEditTarget] = useState<ConnectedAppResponse | null>(null);
  const [editState, setEditState] = useState<ConnectedAppPermissionState | null>(null);
  const [folders, setFolders] = useState<McpFolderOption[]>([]);
  const [foldersLoading, setFoldersLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  // Folders are only needed once the editor opens, so they are fetched lazily
  // and only once.
  const foldersRequested = useRef(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .listConnectedApps()
      .then((res) => active && setApps(res.apps))
      .catch(() => {
        /* the list may simply be empty */
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function refreshApps() {
    const list = await api.listConnectedApps();
    setApps(list.apps);
  }

  /**
   * Load the folder list for the picker, once.
   *
   * Fail-soft: if it cannot be loaded the picker stays empty and the user can
   * still keep "All folders"; only switching to "one folder" is blocked, with
   * the validation message explaining why.
   */
  function ensureFolders() {
    if (foldersRequested.current) return;
    foldersRequested.current = true;
    setFoldersLoading(true);
    api
      .listFolders()
      .then((res) => setFolders(res.folders.map((f) => ({ folder_id: f.id, name: f.name }))))
      .catch(() => {
        /* the picker simply stays empty */
      })
      .finally(() => setFoldersLoading(false));
  }

  function openEditor(app: ConnectedAppResponse) {
    setEditTarget(app);
    setEditState(initialPermissionState(app));
    ensureFolders();
  }

  function closeEditor() {
    setEditTarget(null);
    setEditState(null);
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    const id = revokeTarget.id;
    try {
      await api.revokeConnectedApp(id);
      await refreshApps();
      success("Access revoked");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not revoke access");
    } finally {
      setRevokeTarget(null);
    }
  }

  async function handleSave(body: UpdateConnectedAppRequest) {
    if (!editTarget) return;
    setSaving(true);
    try {
      await api.updateConnectedApp(editTarget.id, body);
      await refreshApps();
      success("Permissions updated");
      closeEditor();
    } catch (err) {
      // Keep the dialog open with the user's input intact, and explain the
      // failure — the API's own wording where it has one.
      error(describeUpdateError(err));
    } finally {
      setSaving(false);
    }
  }

  const active = apps.filter((a) => a.status === "ACTIVE");
  // A paused connection is neither working nor revoked: it keeps its scopes and
  // the user can resume it, so it must not be shown as "Active" (which would
  // contradict the Developer > MCP Activity page) nor as "Revoked".
  const paused = apps.filter((a) => a.status === "PAUSED");
  const revoked = apps.filter((a) => a.status === "REVOKED");

  return (
    <>
      <Card className="p-6">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">AI apps</h2>
          <PlugsConnected size={20} className="text-muted" aria-hidden="true" />
        </div>
        <p className="mb-4 text-sm text-muted">
          Applications you have connected so an AI assistant can read, convert and save your files.
          Revoking one takes effect immediately.
        </p>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton className="h-4 flex-1" />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        ) : apps.length === 0 ? (
          <p className="text-sm text-muted">No applications are connected.</p>
        ) : (
          <ul className="divide-y divide-outline">
            {[...active, ...paused, ...revoked].map((app) => (
              <ConnectedAppRow
                key={app.id}
                app={app}
                onEdit={openEditor}
                onRevoke={setRevokeTarget}
              />
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke access?"
        maxWidth="max-w-md"
      >
        <p className="mb-4 text-sm text-muted">
          {revokeTarget?.client_name} will lose access to your files immediately. You can connect it
          again later if you change your mind.
        </p>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleRevoke}>
            Revoke access
          </Button>
        </div>
      </Modal>

      <Modal
        open={editTarget !== null}
        onClose={() => {
          // Don't let the backdrop or Escape drop the dialog mid-save.
          if (!saving) closeEditor();
        }}
        title={editTarget ? `Edit ${editTarget.client_name} permissions` : "Edit permissions"}
        maxWidth="max-w-lg"
      >
        {editTarget && editState && (
          <ConnectedAppPermissionsEditor
            app={editTarget}
            state={editState}
            onChange={setEditState}
            folders={folders}
            foldersLoading={foldersLoading}
            saving={saving}
            onSave={handleSave}
            onClose={closeEditor}
          />
        )}
      </Modal>
    </>
  );
}
