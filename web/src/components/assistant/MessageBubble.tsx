import { memo, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Sparkle, PencilSimple } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import type { AssistantChatMessage } from "@/lib/assistantChat";
import { isImeComposing } from "@/lib/keyboard";
import { toolStepLabel } from "@/lib/assistantTranscript";
import { ArtifactChips } from "@/components/assistant/ArtifactChips";
import { AttachmentChips } from "@/components/assistant/AttachmentChips";
import { MessageActions } from "@/components/assistant/MessageActions";
import { RichText } from "@/components/assistant/RichText";
import { ToolSteps } from "@/components/assistant/ToolSteps";
import { Button } from "@/components/ui";

/**
 * One message in the transcript.
 *
 * Assistant text goes through `RichText`, which renders the light Markdown the
 * model emits (`**bold**`, bullets, headings) as real elements — never as HTML
 * and never as literal asterisks. A reopened turn arrives as a single assistant
 * message carrying its `steps`; those render as a quiet disclosure above the
 * answer rather than as their own bubbles.
 *
 * No code path renders a tool message's `content`: that is the raw tool-result
 * JSON. The `tool` branch below is defensive only (the transcript builder folds
 * tool rows into steps before they reach here) and shows a neutral label built
 * from the tool name.
 *
 * A user message is rendered by `UserMessageBubble`, which owns the inline
 * editor. `onEdit` is optional: without it (the mini chat, or a surface that has
 * not wired editing yet) the affordance is simply absent.
 *
 * Memoised because the transcript re-renders on every streamed delta: the
 * reducer replaces only the pending message object, so every *settled*
 * message's `message` prop keeps its identity. `MessageList` passes
 * referentially-stable `onRetry`/`onEdit` (ref-backed callbacks), which is what
 * makes this `memo` actually skip work instead of re-rendering N bubbles per
 * token. A pending message's object changes each delta, so it still updates.
 */
export const MessageBubble = memo(function MessageBubble({
  message,
  onRetry,
  onEdit,
}: {
  message: AssistantChatMessage;
  onRetry?: (messageId: string) => void;
  onEdit?: (messageId: string, text: string, attachments: AssistantAttachment[]) => void;
}) {
  if (message.role === "tool") {
    return <p className="px-1 text-xs text-muted">{toolStepLabel(message.toolName)}</p>;
  }

  // A sent question is a bubble on the right; an answer is prose on the
  // surface, introduced by the AI glyph and with no enclosing box — the modern,
  // product-native read rather than two symmetric chat bubbles.
  if (message.role === "user") {
    return <UserMessageBubble message={message} onEdit={onEdit} />;
  }

  return (
    <div className="group flex min-w-0 items-start gap-3">
      <AssistantGlyph pending={message.pending} />
      <div
        // A pending assistant turn is the live region the stream writes into,
        // so a screen reader hears the answer as it arrives rather than only
        // when the turn finishes. `aria-atomic="false"` keeps it to the new
        // text instead of re-reading the whole message on every frame.
        aria-live={message.pending ? "polite" : undefined}
        aria-atomic={message.pending ? false : undefined}
        // `min-w-0` lets this flex item shrink below its content's intrinsic
        // width, which is what keeps a long artifact chip inside the column.
        className="min-w-0 flex-1 space-y-2 pt-0.5 text-sm text-on-background"
      >
        {message.steps && message.steps.length > 0 && <ToolSteps steps={message.steps} />}
        <RichText text={message.content} streaming={message.pending} />
        {message.artifacts.length > 0 && <ArtifactChips artifacts={message.artifacts} />}
        {!message.pending && message.content.trim() && (
          <MessageActions
            content={message.content}
            onRetry={onRetry ? () => onRetry(message.id) : undefined}
          />
        )}
      </div>
    </div>
  );
});

/**
 * A question the user sent, with its inline editor.
 *
 * The Edit affordance follows the same rule as the answer's actions: hidden
 * until hover or focus on a fine pointer (there is no room to spare on a
 * phone-sized bubble), always visible on a coarse pointer, and always reachable
 * by keyboard because `group-focus-within` reveals it as soon as it takes focus.
 */
function UserMessageBubble({
  message,
  onEdit,
}: {
  message: AssistantChatMessage;
  onEdit?: (messageId: string, text: string, attachments: AssistantAttachment[]) => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing && onEdit) {
    return (
      <MessageEditor
        message={message}
        onCancel={() => setEditing(false)}
        onResend={(text, attachments) => {
          // Close first so a slow resend never leaves the editor open over the
          // turn that is already replacing it.
          setEditing(false);
          onEdit(message.id, text, attachments);
        }}
      />
    );
  }

  return (
    <div className="group flex min-w-0 items-end justify-end gap-2">
      {onEdit && (
        <button
          type="button"
          onClick={() => setEditing(true)}
          aria-label="Edit message"
          title="Edit"
          className="shrink-0 rounded-md p-1.5 text-muted opacity-100 transition-opacity hover:bg-surface-variant hover:text-on-background pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
        >
          <PencilSimple size={14} aria-hidden />
        </button>
      )}
      <div className="min-w-0 max-w-[85%] space-y-2 rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-on-primary sm:max-w-[75%]">
        {message.attachments.length > 0 && (
          <AttachmentChips attachments={message.attachments} collapsible tone="onPrimary" />
        )}
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
    </div>
  );
}

/**
 * The in-place editor a user message opens into.
 *
 * It takes the bubble's place rather than floating over it, so the transcript
 * does not jump and the edited turn stays exactly where it was read. Enter
 * resends, Escape cancels (a textarea, so Shift+Enter still adds a line), and a
 * whitespace-only message cannot be resent — the resend would be a blank turn.
 */
function MessageEditor({
  message,
  onResend,
  onCancel,
}: {
  message: AssistantChatMessage;
  onResend: (text: string, attachments: AssistantAttachment[]) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(message.content);
  const [attachments, setAttachments] = useState<AssistantAttachment[]>(() =>
    message.attachments.map((attachment) => ({ ...attachment })),
  );
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const fieldId = `edit-message-${message.id}`;

  // Move focus in and put the caret at the end, so the user can keep typing
  // immediately rather than having to click into the field they just opened.
  useEffect(() => {
    const element = areaRef.current;
    if (!element) return;
    element.focus();
    const end = element.value.length;
    element.setSelectionRange(end, end);
  }, []);

  const canResend = text.trim().length > 0;

  function resend() {
    if (!canResend) return;
    onResend(text.trim(), attachments);
  }

  return (
    <div className="flex min-w-0 justify-end">
      <form
        aria-label="Edit message"
        onSubmit={(event) => {
          event.preventDefault();
          resend();
        }}
        className="w-full min-w-0 max-w-[85%] space-y-2 rounded-2xl rounded-br-md border border-primary/50 bg-surface p-3 sm:max-w-[75%]"
      >
        <label htmlFor={fieldId} className="sr-only">
          Edit message
        </label>
        <textarea
          id={fieldId}
          ref={areaRef}
          value={text}
          rows={3}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter confirms an IME candidate, so a composition must never be
            // read as "resend" — the same rule the composer follows.
            if (isImeComposing(event.nativeEvent)) return;
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              resend();
            } else if (event.key === "Escape") {
              event.preventDefault();
              onCancel();
            }
          }}
          className="w-full resize-none rounded-lg border border-outline bg-surface-variant/50 px-3 py-2 text-sm text-on-background outline-none focus:border-primary"
        />
        {attachments.length > 0 && (
          <AttachmentChips
            attachments={attachments}
            onRemove={(id) =>
              setAttachments((previous) => previous.filter((attachment) => attachment.id !== id))
            }
          />
        )}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onCancel}
            className="pointer-coarse:min-h-11"
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={!canResend}
            className="pointer-coarse:min-h-11"
          >
            Resend
          </Button>
        </div>
      </form>
    </div>
  );
}

/**
 * The Transform AI mark beside an answer.
 *
 * It shimmers while the turn is still being written and settles into a solid
 * brand tile afterwards; the animation is dropped entirely under reduced
 * motion, where the caret in the text already signals that more is coming.
 */
function AssistantGlyph({ pending }: { pending: boolean }) {
  const reduce = useReducedMotion();
  return (
    <span
      className="relative mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-outline bg-primary-container text-primary"
      aria-hidden
    >
      <Sparkle size={15} weight="fill" />
      {pending && !reduce && (
        <motion.span
          className="pointer-events-none absolute inset-0 bg-gradient-to-r from-transparent via-primary/40 to-transparent"
          initial={{ x: "-100%" }}
          animate={{ x: "100%" }}
          transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
        />
      )}
    </span>
  );
}
