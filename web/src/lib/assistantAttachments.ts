// Attachments for a Transform AI turn.
//
// The composer caps how many files a single turn can carry, keeps the list
// free of duplicates, and sends the ids as `file_ids`. All of that decision
// logic lives here (pure, no DOM) so it can be tested in the Node environment
// and reused by both the full assistant page and the floating mini chat.

import type { AssistantAttachment } from "@/api/types";

/** How many files one turn may attach. Mirrors the API's `file_ids` limit. */
export const MAX_ASSISTANT_ATTACHMENTS = 5;

/**
 * The extension of a file name, or `""` when it has none.
 *
 * `fileNameExtension` (which the rest of the app uses) returns the whole name
 * for a dotless file — fine where the name is known to be `name.ext`, but here
 * it would give `LICENSE` the format "license" and draw the wrong thumbnail.
 * A leading dot (a dotfile like `.env`) is not an extension either.
 */
function extensionFromName(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot >= name.length - 1) return "";
  return name.slice(dot + 1).toLowerCase();
}

/** The extension (lowercase, dot-free) to draw the chip's thumbnail from. */
export function attachmentExtension(attachment: AssistantAttachment): string {
  const declared = (attachment.extension ?? "").trim().toLowerCase().replace(/^\./, "");
  if (declared) return declared;
  return extensionFromName(attachment.name) || "txt";
}

/** A name to render, falling back to the id when the server echoed none. */
export function attachmentLabel(attachment: AssistantAttachment): string {
  const name = attachment.name.trim();
  return name || attachment.id || "Attachment";
}

/** Build the composer's view of a library file. */
export function attachmentFromFile(file: { id: string; file_name: string }): AssistantAttachment {
  const extension = extensionFromName(file.file_name);
  return extension
    ? { id: file.id, name: file.file_name, extension }
    : { id: file.id, name: file.file_name };
}

export type AttachOutcome = "added" | "duplicate" | "full";

export interface AttachResult {
  attachments: AssistantAttachment[];
  /**
   * Why the list did or did not change:
   *   - `added`     — the file is now attached;
   *   - `duplicate` — it was already attached (ids are unique);
   *   - `full`      — the cap was reached and nothing changed.
   */
  outcome: AttachOutcome;
}

export function canAttachMore(
  current: readonly AssistantAttachment[],
  max = MAX_ASSISTANT_ATTACHMENTS,
): boolean {
  return current.length < max;
}

/**
 * Add `next` to an attachment list.
 *
 * Duplicates are a no-op keyed on the file id (never the name: two files may
 * share a name, and the same file can be re-picked after being removed). At the
 * cap the list is returned unchanged with `outcome: "full"`, so the caller can
 * explain why nothing happened rather than silently dropping the file.
 */
export function attachAssistantFile(
  current: readonly AssistantAttachment[],
  next: AssistantAttachment,
  max = MAX_ASSISTANT_ATTACHMENTS,
): AttachResult {
  if (current.some((attachment) => attachment.id === next.id)) {
    return { attachments: [...current], outcome: "duplicate" };
  }
  if (current.length >= max) {
    return { attachments: [...current], outcome: "full" };
  }
  return { attachments: [...current, next], outcome: "added" };
}

/** Remove one attachment by id. Unknown ids are a no-op. */
export function removeAssistantAttachment(
  current: readonly AssistantAttachment[],
  id: string,
): AssistantAttachment[] {
  return current.filter((attachment) => attachment.id !== id);
}

/** The `file_ids` a turn should send; empty means "no attachments key". */
export function attachmentFileIds(current: readonly AssistantAttachment[]): string[] {
  return current.map((attachment) => attachment.id).filter((id) => id !== "");
}

/**
 * Drop attachments the server says are gone, keeping the rest.
 *
 * Used on an `ATTACHMENT_NOT_FOUND` response: the offending ids are removed
 * from the composer so a resend sends only the files that still exist.
 */
export function withoutAttachmentIds(
  current: readonly AssistantAttachment[],
  gone: readonly string[],
): AssistantAttachment[] {
  const drop = new Set(gone);
  return current.filter((attachment) => !drop.has(attachment.id));
}
