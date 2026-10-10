/**
 * Pure helpers for the Files page's "an agent is using this folder" indicator.
 *
 * The indicator must be quiet and must never take the page down, so everything
 * that is not markup — turning the endpoint's rows into a lookup, and wording
 * the accessible label — lives here where it can be unit-tested without a DOM.
 */

import type { McpFolderAccessEntry } from "@/api/types";

/**
 * Map each folder an active agent may reach to that agent's display name.
 *
 * First writer wins when more than one grant reaches the same folder: the
 * indicator names one application, and picking the first makes it stable rather
 * than dependent on response ordering. Rows without a folder id or a client
 * name are skipped — a nameless badge is worse than no badge.
 */
export function buildAgentFolderMap(entries: McpFolderAccessEntry[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const entry of entries) {
    if (!entry.folder_id || !entry.client_name) continue;
    if (!map.has(entry.folder_id)) map.set(entry.folder_id, entry.client_name);
  }
  return map;
}

/**
 * The accessible name for a folder's agent indicator, e.g.
 * "Used by Claude Desktop".
 *
 * A full sentence rather than the bare application name so the badge is
 * understandable out of context — a screen reader user meeting a lone icon in a
 * folder row has no other way to know what it means.
 */
export function agentFolderLabel(clientName: string): string {
  const name = clientName.trim();
  return name ? `Used by ${name}` : "Used by an AI app";
}
