/**
 * Pure rules for editing an existing AI-application connection in place.
 *
 * Settings → AI apps lets a user change what a connected application may do,
 * and the parts of that decision that have a right answer independent of the
 * markup live here: whether the selection can be saved, exactly what body is
 * sent, and — the easy part to get wrong — when the change actually takes
 * effect. Keeping them pure means they can be asserted directly, without a
 * renderer (this project has no jsdom; see `lib/mcpConsentRequest.test.ts`).
 *
 * The editor is *fully controlled* by its caller (`ConnectedAppsSection`), so
 * the render test can hand it any selection rather than only the one the app
 * happens to start with.
 */

import type {
  ConnectedAppResponse,
  McpFolderAccess,
  McpHistoryScope,
  UpdateConnectedAppRequest,
} from "@/api/types";
import { HISTORY_SCOPE_LABELS } from "@/lib/mcpConsentRequest";

/**
 * Human wording for a scope, so no screen prints a raw identifier.
 *
 * The single source for both the settings row and its editor.
 */
export const SCOPE_LABELS: Record<string, string> = {
  "documents.read": "Read files",
  "documents.convert": "Convert",
  "documents.write": "Save files",
  "documents.delete": "Delete files",
};

/** The permissions the editor offers, in the order they are shown. */
export const PERMISSION_SCOPES = [
  "documents.read",
  "documents.convert",
  "documents.write",
  "documents.delete",
] as const;

/**
 * The one permission that allows an irreversible action.
 *
 * The consent screen learns this from the server's `destructive` flag, but the
 * editor has to branch on it *locally* to require an explicit confirmation, so
 * the name is pinned here rather than compared against a string literal at the
 * call site.
 */
export const DESTRUCTIVE_SCOPE = "documents.delete";

/** Human wording for a scope, falling back to the raw identifier. */
export function scopeLabel(scope: string): string {
  return SCOPE_LABELS[scope] ?? scope;
}

/** The permission selection the editor is working on (fully controlled). */
export interface ConnectedAppPermissionState {
  scopes: string[];
  folderAccess: McpFolderAccess;
  /** The chosen folder id; required when `folderAccess` is `FOLDER`. */
  folderId: string | null;
  historyScope: McpHistoryScope;
  /** Whether the user ticked the extra confirmation for a newly added delete. */
  confirmDestructive: boolean;
}

/**
 * Seed the editor from the connection's current values (all pre-selected).
 *
 * Least access wins: an existing `FOLDER` binding stays `FOLDER`, so merely
 * opening the editor can never silently widen an app to the whole Drive. As on
 * the consent screen, the destructive confirmation is never pre-ticked.
 */
export function initialPermissionState(app: ConnectedAppResponse): ConnectedAppPermissionState {
  return {
    scopes: [...app.scopes],
    folderAccess: app.folder_access === "FOLDER" ? "FOLDER" : "ALL",
    folderId: app.folder_id ?? null,
    historyScope: app.history_scope === "ALL" ? "ALL" : "AGENT",
    confirmDestructive: false,
  };
}

/** Add or remove one scope, preserving the order of the rest. */
export function toggleScope(scopes: string[], scope: string): string[] {
  return scopes.includes(scope) ? scopes.filter((s) => s !== scope) : [...scopes, scope];
}

/**
 * Whether the edit is *adding* `documents.delete` where it was not granted.
 *
 * Removing delete must never trigger the guard: the confirmation exists only to
 * make a new, irreversible capability a deliberate choice.
 */
export function requiresDestructiveConfirmation(
  originalScopes: string[],
  nextScopes: string[],
): boolean {
  return nextScopes.includes(DESTRUCTIVE_SCOPE) && !originalScopes.includes(DESTRUCTIVE_SCOPE);
}

/** The outcome of checking whether the selection can be saved. */
export interface PermissionValidation {
  ok: boolean;
  /** A sentence to show when `ok` is false, else null. */
  message: string | null;
}

/**
 * Whether the selection is complete enough to send, ignoring the destructive
 * confirmation (checked separately so the two reasons stay distinguishable).
 */
export function validatePermissionSelection(
  state: ConnectedAppPermissionState,
): PermissionValidation {
  // A connection with no permissions can do nothing, and the server rejects it;
  // the editor says so before spending a round trip.
  if (state.scopes.length === 0) {
    return { ok: false, message: "Choose at least one permission for this app." };
  }
  // "One folder" with nothing picked is a body the API answers 400 to.
  if (state.folderAccess === "FOLDER" && !state.folderId) {
    return { ok: false, message: "Choose which folder this app may use." };
  }
  return { ok: true, message: null };
}

/**
 * The full save readiness: the selection must be valid AND, when delete is
 * newly added, the user must have ticked the confirmation.
 */
export function validatePermissionEdit(input: {
  state: ConnectedAppPermissionState;
  original: ConnectedAppResponse;
}): PermissionValidation {
  const selection = validatePermissionSelection(input.state);
  if (!selection.ok) return selection;
  if (
    requiresDestructiveConfirmation(input.original.scopes, input.state.scopes) &&
    !input.state.confirmDestructive
  ) {
    return { ok: false, message: "Confirm the delete permission before saving." };
  }
  return { ok: true, message: null };
}

/**
 * The PATCH body for the current selection, or null when it is not savable.
 *
 * Two guarantees the API relies on, which no caller should have to re-check:
 *  - `folder_id` is null whenever `folder_access` is `ALL` — switching back to
 *    the whole Drive can never leave a stale folder id in the payload;
 *  - `confirm_destructive` is true only when `documents.delete` is being newly
 *    added — removing it, or keeping an existing grant, never sends it.
 */
export function buildUpdateConnectedAppRequest(input: {
  state: ConnectedAppPermissionState;
  original: ConnectedAppResponse;
}): UpdateConnectedAppRequest | null {
  if (!validatePermissionEdit(input).ok) return null;
  const { state, original } = input;
  return {
    scopes: [...state.scopes],
    folder_access: state.folderAccess,
    // Never widen silently: an `ALL` choice discards any remembered folder id.
    folder_id: state.folderAccess === "FOLDER" ? state.folderId : null,
    history_scope: state.historyScope,
    confirm_destructive: requiresDestructiveConfirmation(original.scopes, state.scopes),
  };
}

/** Which way an edit moves access, if at all. */
export type PermissionChangeDirection = "none" | "narrowing" | "widening" | "both";

/** What the user should expect after saving, in plain language. */
export interface PermissionChangeEffect {
  direction: PermissionChangeDirection;
  /** The sentence(s) to show, or null when nothing that changes reach was edited. */
  message: string | null;
}

/**
 * The honest statement of *when* a change takes effect, per direction.
 *
 * A reduction is enforced as soon as the app next asks (and the app then has to
 * re-authorize, because what it holds no longer covers what it needs); an
 * addition does nothing at all until the app re-authorizes or refreshes. The
 * two must never be flattened into "changes are instant", which would be a
 * promise the product cannot keep for an addition.
 */
export const PERMISSION_NARROWING_MESSAGE =
  "Reduced access takes effect on the app's next request. The app will need to re-authorize, because its current access stops working.";
export const PERMISSION_WIDENING_MESSAGE =
  "Added access does not start working until the app re-authorizes or refreshes its connection.";

/**
 * Whether an edit reduces what the app can reach.
 *
 * A confined folder swapped for a *different* folder counts as narrowing: the
 * set of files it can reach changes, so the app has to re-authorize rather than
 * keep using a token scoped to the old folder.
 */
function isNarrowing(original: ConnectedAppResponse, state: ConnectedAppPermissionState): boolean {
  const before = new Set(original.scopes);
  const after = new Set(state.scopes);
  for (const scope of before) if (!after.has(scope)) return true;
  if (original.folder_access === "ALL" && state.folderAccess === "FOLDER") return true;
  if (
    original.folder_access === "FOLDER" &&
    state.folderAccess === "FOLDER" &&
    (original.folder_id ?? null) !== (state.folderId ?? null)
  ) {
    return true;
  }
  if (original.history_scope === "ALL" && state.historyScope === "AGENT") return true;
  return false;
}

/** Whether an edit increases what the app can reach. */
function isWidening(original: ConnectedAppResponse, state: ConnectedAppPermissionState): boolean {
  const before = new Set(original.scopes);
  const after = new Set(state.scopes);
  for (const scope of after) if (!before.has(scope)) return true;
  if (original.folder_access === "FOLDER" && state.folderAccess === "ALL") return true;
  if (original.history_scope === "AGENT" && state.historyScope === "ALL") return true;
  return false;
}

/** What to tell the user about when the edit takes effect. */
export function describePermissionChange(input: {
  state: ConnectedAppPermissionState;
  original: ConnectedAppResponse;
}): PermissionChangeEffect {
  const narrowing = isNarrowing(input.original, input.state);
  const widening = isWidening(input.original, input.state);
  if (!narrowing && !widening) return { direction: "none", message: null };
  if (narrowing && widening) {
    return {
      direction: "both",
      message: `${PERMISSION_NARROWING_MESSAGE} ${PERMISSION_WIDENING_MESSAGE}`,
    };
  }
  return narrowing
    ? { direction: "narrowing", message: PERMISSION_NARROWING_MESSAGE }
    : { direction: "widening", message: PERMISSION_WIDENING_MESSAGE };
}

/** A short, honest label for where the app may work, for the settings row. */
export function describeFolderConfinement(app: {
  folder_access: McpFolderAccess;
  folder_name: string | null;
}): string {
  if (app.folder_access !== "FOLDER") return "All folders";
  // `folder_name` is null both for `ALL` and for a folder that no longer
  // exists; here it can only mean the latter, so name that rather than render
  // an empty badge or the literal "null".
  return app.folder_name ? `Folder: ${app.folder_name}` : "Folder removed";
}

/** A short label for the history scope, reusing the pinned consent wording. */
export function describeHistoryConfinement(scope: McpHistoryScope): string {
  return HISTORY_SCOPE_LABELS[scope];
}

/**
 * A readable message for a failed permission update.
 *
 * Prefers the API's own wording (its 400 detail is written for a person), and
 * falls back to status-specific copy for the two states the user can actually
 * act on. Never surfaces a bare "Request failed" or a raw stack.
 */
export function describeUpdateError(err: unknown): string {
  const shape = err as { status?: unknown; message?: unknown } | null;
  const status = shape?.status;
  // `ApiError` is an `Error`, but a plain object is accepted too so the rule is
  // testable without constructing the client's error class.
  const raw = err instanceof Error ? err.message : shape?.message;
  const message = typeof raw === "string" ? raw.trim() : "";
  if (status === 409) {
    return "This connection has already been revoked. Refresh the list to see the current state.";
  }
  if (status === 404) {
    return "This app is no longer connected. Refresh the list and try again.";
  }
  if (status === 400) {
    return message || "Those permissions are not valid for this app.";
  }
  return message || "Could not update permissions. Please try again.";
}
