import { useState } from "react";
import {
  ArrowCounterClockwise,
  CircleNotch,
  Question,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";
import type { AssistantArtifact } from "@/api/types";
import { ASSISTANT_DELETION_NOT_FOUND } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { FormatThumb } from "@/components/FormatThumb";
import { Button } from "@/components/ui";
import { deletionView, type DeletionRecord } from "@/lib/assistantDeletion";
import { formatBytes } from "@/lib/format";

/**
 * The assistant's request to delete a file — the only destructive action it can
 * reach, and the only reason it needs the user to answer.
 *
 * It is a full-width card inside the answer rather than a chip, because a chip
 * is a label and this is a decision: the model chose the target, so the user has
 * to be shown what will be deleted and given an unambiguous way to stop it. The
 * copy is deliberately blunt for the same reason — "Delete this file?" under a
 * file name, with the permanence stated in the body, not softened into a
 * "remove" that could be read as reversible.
 *
 * The decision itself is a single call (approve/reject) and its result is held
 * locally: the artifact in the transcript is a snapshot from when the proposal
 * was made and is not refetched, so without `resolvedState` a successful
 * deletion would leave the buttons on screen. A *reload* takes the other path —
 * the server persists `meta.state`, and `deletionView` renders that as a record
 * (see `lib/assistantDeletion.ts`), so the card can never show a live prompt for
 * a file that is already gone.
 */
export function DeletionCard({ artifact }: { artifact: AssistantArtifact }) {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();

  const [submitting, setSubmitting] = useState(false);
  // The endpoint's answer, which outranks the artifact's snapshot.
  const [resolvedState, setResolvedState] = useState<string | null>(null);
  // A failure that is *not* terminal (a dropped connection), shown inline as
  // well as toasted so the card does not sit silent and unchanged — and cleared
  // on the next attempt.
  const [actionError, setActionError] = useState<string | null>(null);

  const view = deletionView(artifact, {
    resolvedState: resolvedState ?? undefined,
    submitting,
  });

  async function decide(approve: boolean) {
    if (view.kind !== "pending") return;
    const conversationId = view.conversationId;
    if (!view.canSubmit || !conversationId) return;

    setSubmitting(true);
    setActionError(null);
    try {
      const result = await client.assistantResolveDeletion(conversationId, {
        file_id: view.fileId,
        approve,
      });
      setResolvedState(result.state);
    } catch (err) {
      const code = (err as { code?: string }).code;
      const message = err instanceof Error ? err.message : "Could not reach the assistant";
      // Surfaced, never swallowed: a destructive control that fails silently
      // reads as a button that did nothing, and the user would press it again.
      toastError(message);
      if (code === ASSISTANT_DELETION_NOT_FOUND) {
        // There is no proposal left to answer (another tab, or an expired one),
        // so the honest state is a resolved record — not a prompt whose every
        // button is guaranteed to fail.
        setResolvedState("failed");
      } else {
        setActionError(message);
      }
    } finally {
      setSubmitting(false);
    }
  }

  const thumb = (
    <FormatThumb format={view.extension || view.name} size="sm" label="" className="shrink-0" />
  );
  const nameLine = (
    <p className="min-w-0 truncate text-xs font-medium text-on-background" title={view.name}>
      {view.name}
    </p>
  );

  if (view.kind === "resolved") {
    return (
      <div
        // `w-full min-w-0` keeps the card inside the assistant column instead of
        // reporting an intrinsic width of its own.
        className="w-full min-w-0 rounded-xl border border-outline bg-surface-variant/40 p-3"
        role="group"
        aria-label={`Deletion record for ${view.name}`}
      >
        <div className="flex min-w-0 items-start gap-2">
          {thumb}
          <div className="min-w-0 flex-1">
            {nameLine}
            <p
              className={`mt-0.5 flex items-center gap-1 text-xs font-semibold ${recordTone(view.outcome)}`}
            >
              {recordIcon(view.outcome)}
              {view.title}
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{view.detail}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      // Error-tinted, not neutral: the frame itself has to say "this is the
      // dangerous one" before the copy is read. The tint uses the design
      // system's `error` token at low alpha, the same treatment the transcript's
      // error banner uses.
      className="w-full min-w-0 space-y-2.5 rounded-xl border border-error/40 bg-error/5 p-3"
      role="group"
      aria-label={`Delete ${view.name}?`}
    >
      <div className="flex min-w-0 items-center gap-2">
        {thumb}
        <div className="min-w-0 flex-1">
          {nameLine}
          {view.sizeBytes != null && (
            <p className="text-[11px] text-muted">{formatBytes(view.sizeBytes)}</p>
          )}
        </div>
        <WarningCircle size={18} weight="fill" className="shrink-0 text-error" aria-hidden />
      </div>

      <div className="space-y-1">
        <p className="text-sm font-semibold text-on-background">{view.question}</p>
        <p className="text-xs leading-relaxed text-muted">{view.detail}</p>
      </div>

      {actionError && (
        <p role="alert" className="min-w-0 break-words text-xs leading-relaxed text-error">
          {actionError}
        </p>
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {/* The destructive choice reads as the primary one — tinted with the
            error token rather than the brand colour — and the escape is the
            neutral button beside it, so neither can be mistaken for the other.
            Both keep a 44px target on a coarse pointer. */}
        <Button
          variant="destructive"
          size="sm"
          onClick={() => void decide(true)}
          disabled={!view.canSubmit}
          aria-label={
            submitting ? `Deleting ${view.name}` : `Permanently delete ${view.name}`
          }
          className="border border-error/50 bg-error/10 hover:bg-error/20 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
        >
          {submitting ? (
            <CircleNotch size={15} className="animate-spin" aria-hidden />
          ) : (
            <Trash size={15} aria-hidden />
          )}
          {submitting ? "Deleting…" : "Delete file"}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void decide(false)}
          disabled={!view.canSubmit}
          aria-label={`Keep ${view.name}`}
          className="pointer-coarse:min-h-11 pointer-coarse:min-w-11"
        >
          Keep file
        </Button>
      </div>
    </div>
  );
}

/** The colour of a resolved record's title line. */
function recordTone(outcome: DeletionRecord): string {
  return outcome === "failed" ? "text-error" : "text-muted";
}

/** The icon beside a resolved record's title. */
function recordIcon(outcome: DeletionRecord) {
  switch (outcome) {
    case "deleted":
      return <Trash size={14} weight="fill" aria-hidden />;
    case "cancelled":
      return <ArrowCounterClockwise size={14} weight="fill" aria-hidden />;
    case "failed":
      return <WarningCircle size={14} weight="fill" aria-hidden />;
    default:
      return <Question size={14} weight="fill" aria-hidden />;
  }
}
