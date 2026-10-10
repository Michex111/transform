import { Info, ShieldCheck } from "@phosphor-icons/react";
import { Button } from "@/components/ui";
import { FolderAccessControl, HistoryScopeControl } from "@/components/developer/ConfinementControls";
import type { ConnectedAppResponse, McpFolderOption, UpdateConnectedAppRequest } from "@/api/types";
import type { ConnectedAppPermissionState } from "@/lib/connectedAppPermissions";
import {
  DESTRUCTIVE_SCOPE,
  PERMISSION_SCOPES,
  buildUpdateConnectedAppRequest,
  describePermissionChange,
  requiresDestructiveConfirmation,
  scopeLabel,
  toggleScope,
  validatePermissionEdit,
} from "@/lib/connectedAppPermissions";

/**
 * The body of the "Edit permissions" dialog for one existing connection.
 *
 * Deliberately fully controlled — it owns no state — so the render test can
 * hand it any selection (including one with a newly added delete grant) rather
 * than only the one the connection happens to start with. `ConnectedAppsSection`
 * holds the selection and reseeds it from the app each time the dialog opens.
 *
 * The folder and history controls are the *same* components the consent screen
 * renders (`components/developer/ConfinementControls`), which is what stops the
 * two screens from describing the same choice in different words.
 *
 * Folder creation is intentionally NOT offered here: the consent screen owns
 * that flow, and a second creation path in an editor that is only meant to
 * adjust an existing grant would be a deliberate widening of scope. A user who
 * needs a new folder creates it in their Drive first.
 */
export function ConnectedAppPermissionsEditor({
  app,
  state,
  onChange,
  folders,
  foldersLoading = false,
  saving,
  onSave,
  onClose,
}: {
  app: ConnectedAppResponse;
  state: ConnectedAppPermissionState;
  onChange: (next: ConnectedAppPermissionState) => void;
  folders: McpFolderOption[];
  foldersLoading?: boolean;
  saving: boolean;
  onSave: (body: UpdateConnectedAppRequest) => void;
  onClose: () => void;
}) {
  const validation = validatePermissionEdit({ state, original: app });
  const effect = describePermissionChange({ state, original: app });
  const needsDestructiveConfirm = requiresDestructiveConfirmation(app.scopes, state.scopes);

  function toggle(scope: string) {
    const scopes = toggleScope(state.scopes, scope);
    // Dropping the delete grant also drops its confirmation, so re-adding it
    // asks again instead of inheriting a stale tick.
    const confirmDestructive = scopes.includes(DESTRUCTIVE_SCOPE)
      ? state.confirmDestructive
      : false;
    onChange({ ...state, scopes, confirmDestructive });
  }

  function save() {
    const body = buildUpdateConnectedAppRequest({ state, original: app });
    // Guarded by `validation`; the early return keeps the invariant local
    // rather than trusting the disabled attribute alone.
    if (!body) return;
    onSave(body);
  }

  return (
    <div>
      <fieldset className="mb-5">
        <legend className="mb-2 text-sm font-medium text-on-background">Permissions</legend>
        <ul className="divide-y divide-outline rounded-lg border border-outline">
          {PERMISSION_SCOPES.map((scope) => (
            <li key={scope} className="flex items-start gap-3 p-3">
              <input
                type="checkbox"
                id={`edit-scope-${scope}`}
                checked={state.scopes.includes(scope)}
                onChange={() => toggle(scope)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
              />
              <label htmlFor={`edit-scope-${scope}`} className="text-sm">
                <span className="block text-on-background">{scopeLabel(scope)}</span>
                <span className="block font-mono text-xs text-muted">{scope}</span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>

      {/* Never pre-ticked, and required to add delete: the consent screen makes
          the same grant a deliberate, extra step, so this editor must too. */}
      {needsDestructiveConfirm && (
        <div className="mb-5 rounded-lg border border-error/40 bg-surface-variant p-3">
          <label className="flex cursor-pointer items-start gap-3 text-sm">
            <input
              type="checkbox"
              id="edit-confirm-destructive"
              checked={state.confirmDestructive}
              onChange={(e) => onChange({ ...state, confirmDestructive: e.target.checked })}
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
            />
            <span>
              <span className="block text-on-background">Let this app delete your files</span>
              <span className="block text-xs text-muted">
                Deleting cannot be undone. Tick this to confirm you want to grant it.
              </span>
            </span>
          </label>
        </div>
      )}

      <FolderAccessControl
        clientName={app.client_name}
        value={state.folderAccess}
        onChange={(folderAccess) => onChange({ ...state, folderAccess })}
        folders={folders}
        folderId={state.folderId}
        onFolderIdChange={(folderId) => onChange({ ...state, folderId })}
        idPrefix="app-edit"
        emptyMessage={
          foldersLoading
            ? "Loading folders…"
            : "You have no folders yet. Create one in your Drive first."
        }
      />

      <HistoryScopeControl
        clientName={app.client_name}
        value={state.historyScope}
        onChange={(historyScope) => onChange({ ...state, historyScope })}
        canChoose
        idPrefix="app-edit"
      />

      {/* When the change takes effect. The two directions genuinely differ, so
          this states the honest one rather than implying both are instant. */}
      {effect.message && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-outline-strong bg-surface-variant p-3">
          <Info size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
          <p className="text-xs text-muted">{effect.message}</p>
        </div>
      )}

      {!validation.ok && validation.message && (
        <p className="mb-4 flex items-start gap-2 text-xs text-muted" role="alert">
          <ShieldCheck size={14} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
          <span>{validation.message}</span>
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || !validation.ok}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </div>
  );
}
