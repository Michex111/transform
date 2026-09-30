// The rules behind the assistant's one destructive action.
//
// The assistant can *propose* deleting a file, but it may never delete one on
// its own: a `delete` artifact arrives with `meta.state = "pending"`, and only
// the user pressing "Delete file" (which calls the deletions endpoint) can turn
// that proposal into a deletion. That is why the artifact is rendered as a full
// card with real buttons rather than a chip — a chip is a label, and this needs
// a decision.
//
// `meta.state` is authoritative, and it is what makes a **reload** honest: once
// the decision has been made the server stores the resulting state, so a
// reopened conversation shows "File deleted" rather than a live prompt for a
// file that is already gone. The mapping from that raw string to what the card
// shows — and the copy in each case — lives here, pure and DOM-free, so every
// branch is testable without rendering anything.

import type { AssistantArtifact } from "@/api/types";
import { fileArtifactExtension } from "@/lib/fileArtifact";

/** The three states the server can resolve a proposal to. */
export type DeletionOutcome = "deleted" | "cancelled" | "failed";

/**
 * The outcome the card shows, including the case where the server sent a state
 * this bundle does not understand.
 */
export type DeletionRecord = DeletionOutcome | "unknown";

/** The proposal is live: the card asks a question and offers two buttons. */
export interface DeletionPendingView {
  kind: "pending";
  fileId: string;
  /** `null` when the artifact carried none — the request cannot be sent. */
  conversationId: string | null;
  /** Trimmed display name, never blank (see `fallbackName`). */
  name: string;
  extension: string;
  sizeBytes: number | null;
  /** The request is in flight; both buttons are disabled. */
  submitting: boolean;
  /** False while submitting, or when `conversationId` is missing. */
  canSubmit: boolean;
  question: string;
  detail: string;
}

/** The decision is behind us (or was never resolvable): a record, no buttons. */
export interface DeletionResolvedView {
  kind: "resolved";
  outcome: DeletionRecord;
  name: string;
  extension: string;
  title: string;
  detail: string;
}

export type DeletionView = DeletionPendingView | DeletionResolvedView;

/** What the card renders when the artifact names no file. */
const fallbackName = "this file";

/**
 * Map one `delete` artifact to the card's view.
 *
 * `resolvedState` is the state the deletions endpoint just returned. It wins
 * over the artifact's `meta.state` because the artifact is a snapshot from when
 * the proposal was made and is not re-fetched after the button is pressed; the
 * response is the only source that knows the decision.
 *
 * A `state` that is neither `"pending"` nor one of the three outcomes resolves
 * to `"unknown"` rather than to a live prompt. For a destructive action the
 * safe degradation is to *not* offer the button: an unrecognised state means we
 * cannot know a proposal exists, and pressing "Delete file" would only ever
 * fail. So the unknown case is non-interactive and says so.
 */
export function deletionView(
  artifact: Pick<AssistantArtifact, "id" | "name" | "meta">,
  options: { resolvedState?: string; submitting?: boolean } = {},
): DeletionView {
  const name = artifact.name.trim() || fallbackName;
  const extension = fileArtifactExtension(artifact);
  // `meta` is server-supplied and unvalidated beyond "is a plain object", so the
  // raw state is read defensively and lowercased before it is compared.
  const raw = options.resolvedState ?? metaString(artifact.meta, "state") ?? "";
  const state = raw.toLowerCase();

  if (state === "pending") {
    const submitting = options.submitting === true;
    const conversationId = metaString(artifact.meta, "conversation_id");
    return {
      kind: "pending",
      fileId: artifact.id,
      conversationId,
      name,
      extension,
      sizeBytes: metaPositiveNumber(artifact.meta, "size_bytes"),
      submitting,
      // Without a conversation id there is no endpoint to call, so the buttons
      // are shown but inert rather than firing a request at a forged path.
      canSubmit: !submitting && conversationId !== null,
      question: "Delete this file?",
      detail: pendingCopy(name).detail,
    };
  }

  const outcome: DeletionRecord =
    state === "deleted" || state === "cancelled" || state === "failed" ? state : "unknown";

  return {
    kind: "resolved",
    outcome,
    name,
    extension,
    ...resolvedCopy(outcome, name),
  };
}

/**
 * The warning line, named after the file when the server named one.
 *
 * The copy is deliberately blunt. This is the only action in the assistant that
 * destroys data, and the model — not the user — chose the target, so "are you
 * sure?" would be the wrong register for it.
 */
function pendingCopy(name: string): { detail: string } {
  const subject = name === fallbackName ? "This file" : `"${name}"`;
  return {
    detail: `${subject} will be permanently deleted from your drive. This can't be undone.`,
  };
}

/** The record's title and one-line explanation, per outcome. */
function resolvedCopy(
  outcome: DeletionRecord,
  name: string,
): { title: string; detail: string } {
  const subject = name === fallbackName ? "The file" : `"${name}"`;
  switch (outcome) {
    case "deleted":
      return { title: "File deleted", detail: `${subject} was permanently deleted.` };
    case "cancelled":
      return { title: "Deletion cancelled", detail: `${subject} was kept.` };
    case "failed":
      // Truthful rather than accusatory: the endpoint answers `failed` for a
      // file the account can no longer see, which is usually a second delete or
      // a file already removed elsewhere.
      return {
        title: "Deletion failed",
        detail: `${subject} could not be deleted — it may already be gone.`,
      };
    default:
      return {
        title: "Deletion status unknown",
        detail: `We couldn't confirm whether ${subject.toLowerCase()} was deleted. Check your files.`,
      };
  }
}

/** A trimmed string from an artifact's untyped `meta`, or `null`. */
function metaString(
  meta: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = meta?.[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** A positive finite number from `meta`, or `null` (never `0` for "unknown"). */
function metaPositiveNumber(
  meta: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  const value = meta?.[key];
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}
