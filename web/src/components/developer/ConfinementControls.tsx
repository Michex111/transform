import type { ReactNode } from "react";
import { FolderSimple, Plus } from "@phosphor-icons/react";
import { Field } from "@/components/ui";
import type { McpFolderAccess, McpFolderOption, McpHistoryScope } from "@/api/types";
import {
  FOLDER_ACCESS_HINTS,
  FOLDER_ACCESS_LABELS,
  HISTORY_SCOPE_HINTS,
  HISTORY_SCOPE_LABELS,
} from "@/lib/mcpConsentRequest";

/**
 * The folder- and history-confinement controls shared by the OAuth consent
 * screen and the in-place permissions editor.
 *
 * Extracted rather than re-implemented: both screens make the same promise to
 * the user ("one folder you choose" really is one folder), so they must render
 * the same options in the same words. Keeping one control means the two cannot
 * drift — the wording lives in `lib/mcpConsentRequest.ts` and these components
 * are the only markup that reads it.
 *
 * Both controls are fully controlled: the screen owns the selection state, so
 * the components stay presentational and testable with `renderToString`.
 */

/** Where the connection may work, with the confined option's folder picker. */
export function FolderAccessControl({
  clientName,
  value,
  onChange,
  folders,
  folderId,
  onFolderIdChange,
  allowCreate = false,
  creatingFolder = false,
  onCreatingFolderChange,
  newFolderName = "",
  onNewFolderNameChange,
  idPrefix = "mcp",
  emptyMessage,
  footer,
}: {
  clientName: string;
  value: McpFolderAccess;
  onChange: (value: McpFolderAccess) => void;
  folders: McpFolderOption[];
  folderId: string | null;
  onFolderIdChange: (folderId: string | null) => void;
  /**
   * Whether to offer "create a new folder for this app".
   *
   * Only the consent screen offers it, because only it has the folder-creation
   * half of the authorize request. The permissions editor leaves it off on
   * purpose (see `ConnectedAppPermissionsEditor`) rather than inventing a
   * second creation flow.
   */
  allowCreate?: boolean;
  creatingFolder?: boolean;
  onCreatingFolderChange?: (creating: boolean) => void;
  newFolderName?: string;
  onNewFolderNameChange?: (name: string) => void;
  /** Distinguishes the radio groups when more than one control shares a page. */
  idPrefix?: string;
  /** Shown in place of the options when the account has no folders to pick. */
  emptyMessage?: string;
  /** Extra content inside the fieldset, e.g. the plain-language summary line. */
  footer?: ReactNode;
}) {
  const pickerLabelId = `${idPrefix}-folder-picker-label`;
  return (
    <fieldset className="mb-5">
      <legend className="mb-2 text-sm font-medium text-on-background">
        Where can {clientName} work?
      </legend>
      <div className="space-y-2">
        {(["ALL", "FOLDER"] as const).map((option) => (
          <label
            key={option}
            className="flex cursor-pointer items-start gap-3 rounded-lg border border-outline p-3 hover:border-primary/40"
          >
            <input
              type="radio"
              name={`${idPrefix}-folder-access`}
              value={option}
              checked={value === option}
              onChange={() => onChange(option)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
            />
            <span className="text-sm">
              <span className="block text-on-background">{FOLDER_ACCESS_LABELS[option]}</span>
              <span className="block text-xs text-muted">{FOLDER_ACCESS_HINTS[option]}</span>
            </span>
          </label>
        ))}
      </div>

      {value === "FOLDER" && (
        <div className="mt-3 border-l-2 border-outline pl-3">
          <p id={pickerLabelId} className="mb-1.5 text-xs font-medium text-muted">
            Folder the agent may use
          </p>
          {/* Bounded height + scroll so a Drive with hundreds of folders is
              still a short, keyboard-navigable list rather than an unbounded
              wall. Native radios give arrow-key navigation for free. */}
          <div
            role="radiogroup"
            aria-labelledby={pickerLabelId}
            className="max-h-56 space-y-0.5 overflow-y-auto rounded-lg border border-outline p-1"
          >
            {folders.map((folder) => (
              <label
                key={folder.folder_id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-surface-variant"
              >
                <input
                  type="radio"
                  name={`${idPrefix}-folder-picker`}
                  value={folder.folder_id}
                  checked={!creatingFolder && folderId === folder.folder_id}
                  onChange={() => {
                    onCreatingFolderChange?.(false);
                    onFolderIdChange(folder.folder_id);
                  }}
                  className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                />
                <FolderSimple size={16} className="shrink-0 text-warning" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{folder.name}</span>
              </label>
            ))}
            {folders.length === 0 && (
              <p className="px-2 py-1.5 text-xs text-muted">
                {emptyMessage ?? "You have no folders yet. Create one for this app below."}
              </p>
            )}
            {allowCreate && (
              <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-surface-variant">
                <input
                  type="radio"
                  name={`${idPrefix}-folder-picker`}
                  value="__new__"
                  checked={creatingFolder}
                  onChange={() => {
                    onCreatingFolderChange?.(true);
                    onFolderIdChange(null);
                  }}
                  className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                />
                <Plus size={16} className="shrink-0 text-primary" aria-hidden="true" />
                <span className="min-w-0 flex-1">Create a new folder for this app</span>
              </label>
            )}
          </div>

          {allowCreate && creatingFolder && (
            <div className="mt-2">
              <Field
                label="New folder name"
                value={newFolderName}
                onChange={(e) => onNewFolderNameChange?.(e.target.value)}
                placeholder="e.g. Claude workspace"
                hint="It is created in your Drive when you allow access."
              />
            </div>
          )}
        </div>
      )}

      {footer}
    </fieldset>
  );
}

/** How much conversion history the connection may read. */
export function HistoryScopeControl({
  clientName,
  value,
  onChange,
  canChoose,
  idPrefix = "mcp",
}: {
  clientName: string;
  value: McpHistoryScope;
  onChange: (value: McpHistoryScope) => void;
  /** When false the one allowed value is stated as text instead of offered. */
  canChoose: boolean;
  idPrefix?: string;
}) {
  return (
    <fieldset className="mb-5">
      <legend className="mb-2 text-sm font-medium text-on-background">
        How much conversion history can {clientName} read?
      </legend>
      {canChoose ? (
        <div className="space-y-2">
          {(["AGENT", "ALL"] as const).map((option) => (
            <label
              key={option}
              className="flex cursor-pointer items-start gap-3 rounded-lg border border-outline p-3 hover:border-primary/40"
            >
              <input
                type="radio"
                name={`${idPrefix}-history-scope`}
                value={option}
                checked={value === option}
                onChange={() => onChange(option)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
              />
              <span className="text-sm">
                <span className="block text-on-background">{HISTORY_SCOPE_LABELS[option]}</span>
                <span className="block text-xs text-muted">{HISTORY_SCOPE_HINTS[option]}</span>
              </span>
            </label>
          ))}
        </div>
      ) : (
        // The API allows only one value here, so the screen states it rather
        // than offering a choice that does not exist.
        <p className="text-sm text-muted">
          {HISTORY_SCOPE_LABELS[value === "ALL" ? "ALL" : "AGENT"]}
          <span className="block text-xs text-muted">This is fixed for this application.</span>
        </p>
      )}
    </fieldset>
  );
}
