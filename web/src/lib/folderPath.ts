import type { FolderResponse } from "@/api/types";

/**
 * Fetches one folder's own record by id.
 *
 * `FilesPage` adapts the folder-contents endpoint to this shape
 * (`client.getFolderContents(id).then((c) => c.folder)`); keeping the module on
 * this narrower contract makes it pure and unit-testable without a DOM or a
 * real API client.
 */
export type FolderFetcher = (folderId: string) => Promise<FolderResponse>;

/**
 * Upper bound on the ancestor walk. A well-formed drive is never this deep; the
 * cap exists so a chain that somehow never reaches a `null` parent (bad data
 * that still passes the cycle check, e.g. a fresh id per hop) fails fast instead
 * of looping.
 */
export const MAX_FOLDER_DEPTH = 50;

/**
 * Resolve a folder id into its ancestor chain, ordered **root → target**, by
 * walking `parent_id` upwards from the target and reversing.
 *
 * Return contract:
 * - `[]` when `targetId` is falsy — there is nothing to resolve, the caller
 *   should stay at the drive root.
 * - `[]` when the id **cannot be resolved**: the id is unknown (the fetcher
 *   rejects, as the API does with a 404), a fetched record's `id` does not match
 *   the id requested (malformed response), a `parent_id` cycle is detected, or
 *   the walk exceeds `MAX_FOLDER_DEPTH`. The caller treats an empty result for a
 *   truthy id as "unknown folder" and falls back to the root.
 * - otherwise the chain, root first, ending with the target — so a root-level
 *   folder yields exactly one entry.
 *
 * A fetcher rejection (unknown id, network/server error) propagates to the
 * caller, which owns turning it into user-facing copy.
 */
export async function buildFolderPath(
  targetId: string | null | undefined,
  fetchFolder: FolderFetcher,
): Promise<FolderResponse[]> {
  if (!targetId) return [];

  const chain: FolderResponse[] = [];
  // Every id we have already fetched on this walk. A parent loop (a→b→a, or an
  // id that is its own parent) re-enters an id recorded here and bails out.
  const seen = new Set<string>();
  let currentId: string | null = targetId;

  while (currentId) {
    if (seen.has(currentId)) return []; // parent_id cycle
    if (chain.length >= MAX_FOLDER_DEPTH) return []; // runaway chain
    seen.add(currentId);

    const folder = await fetchFolder(currentId);
    // Defensive: a record that does not carry the id we asked for is not a
    // usable ancestor, so the chain is unresolvable rather than wrong.
    if (!folder || folder.id !== currentId) return [];

    chain.push(folder);
    currentId = folder.parent_id;
  }

  return chain.reverse();
}
