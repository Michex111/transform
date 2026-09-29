// The rules behind linking an assistant artifact back into the drive.
//
// Pure and DOM-free on purpose: the chips that consume these rules need React,
// a router, a modal and the auth client to run, and this repo has **no jsdom**,
// so anything living inside the component would be untestable here. The
// decisions that actually matter — where a file's "Go to folder" points, what
// extension a thumbnail should show, and what counts as the drive root — are
// plain string logic, so they live here where vitest can pin every branch.

import type { AssistantArtifact } from "@/api/types";
import { fileNameExtension } from "@/lib/format";

/** The app route for the drive. */
const DRIVE_HREF = "/app/files";

/**
 * A trimmed string from an artifact's untyped `meta`, or `null`.
 *
 * `meta` is server-supplied and unvalidated beyond "is a plain object", so a
 * value can be any JSON type. An empty or whitespace-only string is treated as
 * absent — it is what a cleared field serialises to, and neither is a usable
 * folder id or extension.
 */
function metaString(meta: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = meta?.[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/**
 * The drive route, deep-linked to a folder when one is named.
 *
 * A *root-level* file (`folder_id === null`) and a missing/unusable id both
 * link to the plain drive rather than to nothing: "Go to folder" on a root file
 * lands on "My Drive", which is exactly where the file is, so the action stays
 * truthful instead of being hidden. The id is trimmed and URL-encoded because
 * it is dropped into a query string — an id with a `&` or a space must not be
 * able to forge a second parameter (`?folder=a&mode=evil`).
 */
export function driveFolderHref(folderId: string | null | undefined): string {
  const id = typeof folderId === "string" ? folderId.trim() : "";
  return id === "" ? DRIVE_HREF : `${DRIVE_HREF}?folder=${encodeURIComponent(id)}`;
}

/**
 * The `folder_id` a file artifact's `meta` carries.
 *
 * `null` means the drive root. It is also what every unusable shape folds into
 * — an absent `meta`, an absent key, an explicit `null`, a cleared `""`, and a
 * non-string from a malformed payload — so a caller never has to tell "the
 * assistant did not say" apart from "the file is at the root".
 */
export function artifactFolderId(
  artifact: Pick<AssistantArtifact, "meta">,
): string | null {
  return metaString(artifact.meta, "folder_id");
}

/**
 * The deep link to the folder a `folder` artifact names.
 *
 * A folder artifact's `id` IS the folder id (its `meta.parent_id` points the
 * other way), so it needs no `meta` read.
 */
export function folderHrefForArtifact(artifact: AssistantArtifact): string {
  return driveFolderHref(artifact.id);
}

/** Normalise an extension for display: `".PDF "` → `"pdf"`, `""` stays `""`. */
function cleanExtension(value: string): string {
  return value.trim().toLowerCase().replace(/^\./, "");
}

/**
 * The extension a file artifact's thumbnail should render.
 *
 * `meta.extension` is authoritative because the server knows how it stored the
 * file (a `report` with no dot in its name can still be a PDF). Only when the
 * artifact predates that field — or it is not a usable string — does the name
 * get parsed, and only then can this return `""`, which `FormatThumb` renders
 * as its neutral "FILE" tile.
 */
export function fileArtifactExtension(
  artifact: Pick<AssistantArtifact, "name" | "meta">,
): string {
  const fromMeta = metaString(artifact.meta, "extension");
  if (fromMeta) return cleanExtension(fromMeta);
  return cleanExtension(fileNameExtension(artifact.name));
}
