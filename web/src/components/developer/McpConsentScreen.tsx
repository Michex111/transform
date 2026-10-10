import { useState } from "react";
import { FolderSimple, LinkBreak, Plus, Robot, ShieldCheck } from "@phosphor-icons/react";
import { Button, Card, Field } from "@/components/ui";
import type { McpConsentRequestResponse } from "@/api/types";
import type { ConsentConfinementPayload, FolderChoice } from "@/lib/mcpConsentRequest";
import {
  FOLDER_ACCESS_HINTS,
  FOLDER_ACCESS_LABELS,
  HISTORY_SCOPE_HINTS,
  HISTORY_SCOPE_LABELS,
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

      {/* Where the agent may work. Two explicit options, in the product's own
          words, with the picker revealed only for the confined one. */}
      <fieldset className="mb-5">
        <legend className="mb-2 text-sm font-medium text-on-background">
          Where can {request.client_name} work?
        </legend>
        <div className="space-y-2">
          {(["ALL", "FOLDER"] as const).map((value) => (
            <label
              key={value}
              className="flex cursor-pointer items-start gap-3 rounded-lg border border-outline p-3 hover:border-primary/40"
            >
              <input
                type="radio"
                name="mcp-folder-access"
                value={value}
                checked={folderAccess === value}
                onChange={() => setFolderAccess(value)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
              />
              <span className="text-sm">
                <span className="block text-on-background">{FOLDER_ACCESS_LABELS[value]}</span>
                <span className="block text-xs text-muted">{FOLDER_ACCESS_HINTS[value]}</span>
              </span>
            </label>
          ))}
        </div>

        {folderAccess === "FOLDER" && (
          <div className="mt-3 border-l-2 border-outline pl-3">
            <p id="mcp-folder-picker-label" className="mb-1.5 text-xs font-medium text-muted">
              Folder the agent may use
            </p>
            {/* Bounded height + scroll so a Drive with hundreds of folders is
                still a short, keyboard-navigable list rather than an unbounded
                wall. Native radios give arrow-key navigation for free. */}
            <div
              role="radiogroup"
              aria-labelledby="mcp-folder-picker-label"
              className="max-h-56 space-y-0.5 overflow-y-auto rounded-lg border border-outline p-1"
            >
              {request.folders.map((folder) => (
                <label
                  key={folder.folder_id}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-surface-variant"
                >
                  <input
                    type="radio"
                    name="mcp-folder-picker"
                    value={folder.folder_id}
                    checked={!creatingFolder && folderId === folder.folder_id}
                    onChange={() => {
                      setCreatingFolder(false);
                      setFolderId(folder.folder_id);
                    }}
                    className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                  />
                  <FolderSimple size={16} className="shrink-0 text-warning" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                </label>
              ))}
              {request.folders.length === 0 && (
                <p className="px-2 py-1.5 text-xs text-muted">
                  You have no folders yet. Create one for this app below.
                </p>
              )}
              <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-surface-variant">
                <input
                  type="radio"
                  name="mcp-folder-picker"
                  value="__new__"
                  checked={creatingFolder}
                  onChange={() => {
                    setCreatingFolder(true);
                    setFolderId(null);
                  }}
                  className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                />
                <Plus size={16} className="shrink-0 text-primary" aria-hidden="true" />
                <span className="min-w-0 flex-1">Create a new folder for this app</span>
              </label>
            </div>

            {creatingFolder && (
              <div className="mt-2">
                <Field
                  label="New folder name"
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="e.g. Claude workspace"
                  hint="It is created in your Drive when you allow access."
                />
              </div>
            )}
          </div>
        )}

        <p className="mt-3 flex items-start gap-2 text-xs text-muted">
          <ShieldCheck
            size={14}
            className="mt-0.5 shrink-0 text-muted"
            aria-hidden="true"
          />
          <span>{summary}</span>
        </p>
      </fieldset>

      {/* How much conversion history the agent may read. */}
      <fieldset className="mb-5">
        <legend className="mb-2 text-sm font-medium text-on-background">
          How much conversion history can {request.client_name} read?
        </legend>
        {request.can_choose_history_scope ? (
          <div className="space-y-2">
            {(["AGENT", "ALL"] as const).map((value) => (
              <label
                key={value}
                className="flex cursor-pointer items-start gap-3 rounded-lg border border-outline p-3 hover:border-primary/40"
              >
                <input
                  type="radio"
                  name="mcp-history-scope"
                  value={value}
                  checked={historyScope === value}
                  onChange={() => setHistoryScope(value)}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                />
                <span className="text-sm">
                  <span className="block text-on-background">{HISTORY_SCOPE_LABELS[value]}</span>
                  <span className="block text-xs text-muted">{HISTORY_SCOPE_HINTS[value]}</span>
                </span>
              </label>
            ))}
          </div>
        ) : (
          // The API allows only one value here, so the screen states it rather
          // than offering a choice that does not exist.
          <p className="text-sm text-muted">
            {HISTORY_SCOPE_LABELS[request.history_scope === "ALL" ? "ALL" : "AGENT"]}
            <span className="block text-xs text-muted">
              This is fixed for this application.
            </span>
          </p>
        )}
      </fieldset>

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
