// Persisted layout preferences for the assistant workspace.
//
// Only one so far: whether the conversation rail is collapsed. It is stored per
// browser rather than per account because it describes how *this* screen is
// shaped — collapsing the rail on a small laptop should not follow the user to
// a desktop, and nothing about it belongs on the server.
//
// The helpers take the storage object as a parameter so the read/write rules can
// be tested in the Node environment (which has no `localStorage`), and every
// access is guarded: storage throws in private-browsing modes and is absent
// entirely under SSR, and a layout preference is never worth an exception.

/** Whether the conversation rail is collapsed. */
export const RAIL_COLLAPSED_KEY = "assistant_rail_collapsed";

/** The subset of `Storage` this module uses. */
export interface PreferenceStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}

/** The browser's storage, or `null` when there is none (or it is unusable). */
function defaultStorage(): PreferenceStorage | null {
  try {
    if (typeof window === "undefined" || !window.localStorage) return null;
    return window.localStorage;
  } catch {
    // Accessing `localStorage` itself throws when the browser blocks it.
    return null;
  }
}

/**
 * Read the rail's collapsed state.
 *
 * Defaults to expanded (`false`) — the rail is useful, and a preference that
 * failed to save must not hide it. Only the exact stored string `"true"` counts
 * as collapsed, so a corrupted or hand-edited value degrades to the default
 * rather than to a hidden panel the user cannot explain.
 */
export function readRailCollapsed(storage: PreferenceStorage | null = defaultStorage()): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(RAIL_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

/** Persist the rail's collapsed state. Failures are ignored by design. */
export function writeRailCollapsed(
  collapsed: boolean,
  storage: PreferenceStorage | null = defaultStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(RAIL_COLLAPSED_KEY, collapsed ? "true" : "false");
  } catch {
    // A layout preference is not worth surfacing an error for; the panel simply
    // returns to its default next time.
  }
}
