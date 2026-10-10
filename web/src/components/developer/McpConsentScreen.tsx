import { useState } from "react";
import { LinkBreak, Robot, ShieldCheck } from "@phosphor-icons/react";
import { Button, Card } from "@/components/ui";
import { FolderAccessControl, HistoryScopeControl } from "@/components/developer/ConfinementControls";
import type { McpConsentRequestResponse } from "@/api/types";
import type { ConsentConfinementPayload, FolderChoice } from "@/lib/mcpConsentRequest";
import {
  buildConsentConfinement,
  describeFolderChoice,
  effectiveHistoryScope,
  initialFolderChoice,
  initialHistoryScope,
  validateFolderChoice,
} from "@/lib/mcpConsentRequest";

/**
 * The body of the OAuth consent screen: the permissions, and the confinement
 * the visitor chooses alongside them.
 *
 * Kept separate from `AuthorizePage` so the screen's markup can be rendered and
 * asserted under `renderToString` (this project has no jsdom); the page keeps
 * only the fetching, the decision, and the browser handoff. The screen owns the
 * folder/history *selection* state, and reports the completed confinement to
 * its caller only when the visitor approves, so the page never has to know how
 * the choice was assembled.
 */
export function McpConsentScreen({
  request,
  selectedScopes,
  onToggleScope,
  submitting,
  onApprove,
  onDeny,
}: {
  request: McpConsentRequestResponse;
  selectedScopes: Set<string>;
  onToggleScope: (scope: string) => void;
  submitting: boolean;
  onApprove: (confinement: ConsentConfinementPayload) => void;
  onDeny: () => void;
}) {
  // Seeded from the API's current binding so re-consenting shows what is
  // already true rather than silently resetting it. `initialFolderChoice`
  // preserves a `FOLDER` binding (least access) instead of snapping to ALL.
  const [folderAccess, setFolderAccess] = useState(
    () => initialFolderChoice(request).folderAccess,
  );
  const [folderId, setFolderId] = useState<string | null>(
    () => initialFolderChoice(request).folderId,
  );
  // Whether the visitor is creating a new folder instead of picking one.
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  // AGENT is the least an agent can read and the only safe default; ALL is a
  // deliberate opt-in and is never pre-selected.
  const [historyScope, setHistoryScope] = useState(() => initialHistoryScope());

  // The one value that actually gets submitted: a stale typed name must not
  // sneak into a payload after the visitor switched back to picking a folder.
  const folderChoice: FolderChoice = {
    folderAccess,
    folderId,
    newFolderName: creatingFolder ? newFolderName : "",
  };

  const validation = validateFolderChoice(folderChoice);
  const folderName =
    request.folders.find((folder) => folder.folder_id === folderId)?.name ?? null;
  // Plain-language statement of exactly what the agent will be able to reach,
  // shown before the visitor approves.
  const summary = describeFolderChoice({ folderChoice, folderName });

  const canApprove = selectedScopes.size > 0 && validation.ok;

  function approve() {
    const confinement = buildConsentConfinement({
      folderChoice,
      historyScope: effectiveHistoryScope(request, historyScope),
    });
    // Guarded by `canApprove`; the early return keeps the invariant local rather
    // than trusting the disabled attribute alone.
    if (!confinement) return;
    onApprove(confinement);
  }

  return (
    <Card className="mx-auto max-w-lg p-6">
      <div className="mb-1 flex items-center gap-3">
        <Robot size={24} className="text-primary" aria-hidden="true" />
        <h1 className="font-display text-lg font-semibold">
          Allow {request.client_name} to use your account?
        </h1>
      </div>
      <p className="mb-5 text-sm text-muted">
        It is asking for permission to work with your Transform files. You can disconnect it at any
        time from Settings → AI apps, without changing your password.
      </p>

      <ul className="mb-5 divide-y divide-outline">
        {request.scopes.map((s) => {
          const requested = s.requested;
          return (
            <li key={s.scope} className="flex items-start gap-3 py-3">
              <input
                type="checkbox"
                id={`scope-${s.scope}`}
                checked={selectedScopes.has(s.scope)}
                disabled={!requested}
                onChange={() => onToggleScope(s.scope)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)] disabled:opacity-40"
              />
              <label
                htmlFor={`scope-${s.scope}`}
                className={requested ? "text-sm" : "text-sm text-muted"}
              >
                <span className="block text-on-background">{s.description}</span>
                <span className="block font-mono text-xs text-muted">
                  {s.scope}
                  {!requested && " · not requested"}
                  {s.already_granted && " · already allowed"}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      {/* Where the agent may work and how much history it may read. Both
          controls are shared with Settings → AI apps' permissions editor
          (`components/developer/ConfinementControls`), so the two screens
          cannot describe the same choice in different words. */}
      <FolderAccessControl
        clientName={request.client_name}
        value={folderAccess}
        onChange={setFolderAccess}
        folders={request.folders}
        folderId={folderId}
        onFolderIdChange={setFolderId}
        allowCreate
        creatingFolder={creatingFolder}
        onCreatingFolderChange={setCreatingFolder}
        newFolderName={newFolderName}
        onNewFolderNameChange={setNewFolderName}
        idPrefix="mcp"
        footer={
          <p className="mt-3 flex items-start gap-2 text-xs text-muted">
            <ShieldCheck size={14} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
            <span>{summary}</span>
          </p>
        }
      />

      <HistoryScopeControl
        clientName={request.client_name}
        value={effectiveHistoryScope(request, historyScope)}
        onChange={setHistoryScope}
        canChoose={request.can_choose_history_scope}
        idPrefix="mcp"
      />

      <div className="mb-5 flex items-start gap-2 rounded-lg border border-outline-strong bg-surface-variant p-3">
        <ShieldCheck size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
        <p className="text-xs text-muted">
          It will only ever see your own files. You can also revoke its access later.
        </p>
      </div>

      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-end">
        {!validation.ok && (
          <p className="text-xs text-muted sm:mr-auto sm:max-w-[16rem]">{validation.message}</p>
        )}
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={onDeny} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={approve} disabled={submitting || !canApprove}>
            {submitting ? "Connecting…" : "Allow access"}
          </Button>
        </div>
      </div>

      <p className="mt-4 flex items-center gap-1 text-xs text-muted">
        <LinkBreak size={12} aria-hidden="true" />
        Sending you to {request.redirect_uri}
      </p>
    </Card>
  );
}
