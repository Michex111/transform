/**
 * Pure rules for the confinement choices on the MCP consent screen.
 *
 * The consent screen decides *whether* an application may act on a user's
 * account (the scope checkboxes) and — now — *over what*: which part of the
 * Drive, and how much conversion history. This module holds the parts of that
 * second decision that have a right answer independent of the markup, so they
 * can be tested directly rather than through a renderer (this project has no
 * jsdom; see `pages/public/RegisterPage.test.tsx`).
 */

import type {
  McpConsentRequestResponse,
  McpFolderAccess,
  McpHistoryScope,
} from "@/api/types";

/**
 * Copy for the two folder-access options.
 *
 * Mirrors the backend's `FOLDER_ACCESS_DESCRIPTIONS`
 * (`src/domain/security/value_object/agent_access_scope.py`): the screen and the
 * API must describe the same choice in the same words, so the wording lives
 * here as data rather than being retyped at the call site.
 */
export const FOLDER_ACCESS_LABELS: Record<McpFolderAccess, string> = {
  ALL: "All folders in your Drive",
  FOLDER: "Only one folder you choose",
};

/** A one-line explanation under each folder-access option. */
export const FOLDER_ACCESS_HINTS: Record<McpFolderAccess, string> = {
  ALL: "The agent can read and work in every folder you own.",
  FOLDER: "You pick one folder; the agent cannot see anything outside it.",
};

/** Copy for the two history-scope options (mirrors `HISTORY_SCOPE_DESCRIPTIONS`). */
export const HISTORY_SCOPE_LABELS: Record<McpHistoryScope, string> = {
  AGENT: "Only conversions it started",
  ALL: "Your entire conversion history",
};

/** A one-line explanation under each history-scope option. */
export const HISTORY_SCOPE_HINTS: Record<McpHistoryScope, string> = {
  AGENT: "The agent sees the files it converted for you, and nothing else.",
  ALL: "The agent can read every conversion in your history, including ones it had no part in.",
};

/** The folder confinement the consent screen is about to submit. */
export interface FolderChoice {
  folderAccess: McpFolderAccess;
  /** The chosen existing folder id, or null when none is chosen. */
  folderId: string | null;
  /** The name of a folder to create, or "" when not creating one. */
  newFolderName: string;
}

/** The part of the consent response that decides the initial folder choice. */
export type FolderChoiceSource = Pick<
  McpConsentRequestResponse,
  "folder_access" | "folder_id"
>;

/** The part of the consent response that decides the history control. */
export type HistoryChoiceSource = Pick<
  McpConsentRequestResponse,
  "history_scope" | "can_choose_history_scope"
>;

/** The confinement fields of the approve request body. */
export interface ConsentConfinementPayload {
  folder_access: McpFolderAccess;
  folder_id: string | null;
  new_folder_name: string | null;
  history_scope: McpHistoryScope;
}

/**
 * The folder choice to start from, preserving the API's current binding.
 *
 * Least access wins: when the API reports `FOLDER` the screen stays on
 * `FOLDER` even though the user has not touched the control yet, so
 * re-consenting can never silently widen a confined agent to the whole Drive.
 */
export function initialFolderChoice(source: FolderChoiceSource): FolderChoice {
  return {
    folderAccess: source.folder_access === "FOLDER" ? "FOLDER" : "ALL",
    folderId: source.folder_id ?? null,
    newFolderName: "",
  };
}

/** The history scope to start from. Never `ALL`: it is an explicit opt-in. */
export function initialHistoryScope(): McpHistoryScope {
  return "AGENT";
}

/**
 * The history scope to submit.
 *
 * When the API does not let the user choose, the only value it allows is
 * submitted unchanged; otherwise the user's selection is honoured.
 */
export function effectiveHistoryScope(
  source: HistoryChoiceSource,
  selected: McpHistoryScope,
): McpHistoryScope {
  if (source.can_choose_history_scope) return selected;
  return source.history_scope === "ALL" ? "ALL" : "AGENT";
}

/** The outcome of checking whether a folder choice can be submitted. */
export interface FolderChoiceValidation {
  ok: boolean;
  /** A sentence to show when `ok` is false, else null. */
  message: string | null;
}

/**
 * Whether the folder choice is complete enough that the API will accept it.
 *
 * This is the rule the approve control is disabled on. It exists here, not only
 * server-side, so the user is told what is missing *before* they submit rather
 * than by a round trip that answers 400.
 */
export function validateFolderChoice(choice: FolderChoice): FolderChoiceValidation {
  if (choice.folderAccess === "ALL") return { ok: true, message: null };
  if (choice.folderId) return { ok: true, message: null };
  if (choice.newFolderName.trim()) return { ok: true, message: null };
  return {
    ok: false,
    message: "Choose a folder, or name a new one, before you allow access.",
  };
}

/**
 * The folder-access fields of the approve body, or null when incomplete.
 *
 * `folder_id` and `new_folder_name` are mutually exclusive by construction: a
 * folder the user picked is sent as `folder_id`, a folder to create as
 * `new_folder_name`, never both. If both are somehow set, the picked folder
 * wins — the folder list is the primary control, and the name field only
 * appears once the user explicitly opts to create one.
 */
export function buildFolderAccessPayload(
  choice: FolderChoice,
): Pick<ConsentConfinementPayload, "folder_access" | "folder_id" | "new_folder_name"> | null {
  if (choice.folderAccess === "ALL") {
    return { folder_access: "ALL", folder_id: null, new_folder_name: null };
  }
  const name = choice.newFolderName.trim();
  if (choice.folderId) {
    return { folder_access: "FOLDER", folder_id: choice.folderId, new_folder_name: null };
  }
  if (name) {
    return { folder_access: "FOLDER", folder_id: null, new_folder_name: name };
  }
  return null;
}

/**
 * The confinement fields of the approve request, or null when the folder
 * choice is incomplete.
 *
 * Returning null rather than a body the API would reject keeps the failure
 * local and testable: the screen disables the approve control on the same rule
 * (`validateFolderChoice`), so a 400 is a bug, not the validation path.
 */
export function buildConsentConfinement(input: {
  folderChoice: FolderChoice;
  historyScope: McpHistoryScope;
}): ConsentConfinementPayload | null {
  const folder = buildFolderAccessPayload(input.folderChoice);
  if (!folder) return null;
  return { ...folder, history_scope: input.historyScope };
}

/** A plain-language statement of exactly what the agent will be able to reach. */
export function describeFolderChoice(input: {
  folderChoice: FolderChoice;
  /** The chosen existing folder's name, when it can be resolved. */
  folderName?: string | null;
}): string {
  const { folderChoice } = input;
  if (folderChoice.folderAccess === "ALL") {
    return "The agent can reach every folder in your Drive.";
  }
  if (folderChoice.folderId) {
    return input.folderName
      ? `The agent can reach only "${input.folderName}".`
      : "The agent can reach only the folder you selected.";
  }
  const name = folderChoice.newFolderName.trim();
  if (name) {
    return `A new folder "${name}" will be created, and the agent can reach only it.`;
  }
  return "Choose a folder to see exactly what the agent can reach.";
}
