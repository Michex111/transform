import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { PaperPlaneRight, Stop } from "@phosphor-icons/react";
import type { AssistantAttachment, FileMetadataResponse } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { AttachmentChips } from "@/components/assistant/AttachmentChips";
import { AttachControl } from "@/components/assistant/AttachControl";
import { DocumentMentionPicker } from "@/components/assistant/DocumentMentionPicker";
import { attachmentFromFile } from "@/lib/assistantAttachments";
import { isImeComposing } from "@/lib/keyboard";
import { mentionQueryAt, stripMention, type MentionQuery } from "@/lib/mention";
import { useDocumentMention } from "@/lib/useDocumentMention";

/** Matches the API's per-message ceiling; the composer enforces it client-side. */
export const COMPOSER_MAX_LENGTH = 4000;

const COUNTER_VISIBLE_FROM = 200;

/**
 * The message composer.
 *
 * A textarea rather than an input so a multi-line question is possible:
 * Enter sends, Shift+Enter inserts a newline. It grows with its content up to a
 * bound, after which it scrolls, so a long message never pushes the transcript
 * off screen. While a turn streams the Send control becomes Stop.
 *
 * It is a single self-contained panel — attachment chips, the paperclip, the
 * textarea and the send control share one surface — so the composer reads as a
 * place to write rather than a row of controls. Attachment state is owned by
 * the caller: the page sends the ids, and restores the message when the server
 * says a file is gone.
 */
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  attachments = [],
  onAttach,
  onRemoveAttachment,
  disabled = false,
  maxAttachments,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  attachments?: AssistantAttachment[];
  onAttach?: (attachment: AssistantAttachment) => void;
  onRemoveAttachment?: (id: string) => void;
  disabled?: boolean;
  /** The plan's attachment cap, forwarded to the paperclip control. */
  maxAttachments?: number;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const reduce = useReducedMotion();
  // Unique per instance: the page and the floating mini chat are on screen at
  // the same time, and a duplicate id would point the label at the wrong box.
  const textareaId = useId();
  const { api: client } = useAuth();

  // The `@` mention the caret is inside, if any. Null means the picker is shut,
  // which is also what stops any search in flight.
  const [mention, setMention] = useState<MentionQuery | null>(null);
  const mentionState = useDocumentMention({
    client,
    query: mention?.query ?? "",
    enabled: mention !== null && onAttach !== undefined,
  });
  const listboxId = `${textareaId}-documents`;
  // A surface with no `onAttach` cannot carry a file reference, so the picker
  // must not open there at all: offering a list the user can choose from and
  // then discarding the choice is worse than never offering it. This also keeps
  // Enter from being claimed for a list that is not showing.
  const pickerVisible = mention !== null && onAttach !== undefined;
  const hasMentionResults = mentionState.results.length > 0;

  // Auto-grow. Height is reset to `auto` before reading `scrollHeight`, because
  // a textarea only ever reports the height it currently has plus overflow — it
  // never shrinks on its own once it has grown.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  /**
   * Recompute the open mention from the live caret position.
   *
   * Read from the DOM rather than from `value`, because this also runs on
   * selection changes (arrow keys, clicks) where the text has not changed but
   * the caret has moved out of — or into — a mention.
   */
  function refreshMention() {
    const el = textareaRef.current;
    if (!el) return;
    setMention(mentionQueryAt(el.value, el.selectionStart ?? el.value.length));
  }

  /**
   * True while an IME is composing text. Shared with the message editor so the
   * two fields cannot disagree about what Enter means (see `@/lib/keyboard`).
   */
  function isComposing(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
    return isImeComposing(event.nativeEvent);
  }

  /** Turn the chosen file into an attachment and remove the typed `@token`. */
  function chooseMentionedFile(file: FileMetadataResponse) {
    if (onAttach) onAttach(attachmentFromFile(file));
    if (mention) onChange(stripMention(value, mention).slice(0, COMPOSER_MAX_LENGTH));
    setMention(null);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // The IME guard comes first: while composing, *every* key belongs to the
    // input method — including Escape, which cancels a composition rather than
    // dismissing the picker.
    if (isComposing(e)) return;

    if (pickerVisible) {
      // Arrow keys and Enter drive the list only while it is open; otherwise
      // they are ordinary text editing. Enter is claimed only when there is a
      // row to choose, so a mention with no matches still submits normally.
      if (e.key === "ArrowDown") {
        e.preventDefault();
        mentionState.moveActive(1);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        mentionState.moveActive(-1);
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && hasMentionResults) {
        e.preventDefault();
        if (mentionState.activeResult) chooseMentionedFile(mentionState.activeResult);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMention(null);
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim() && !streaming && !disabled) onSend();
    }
  }

  const remaining = COMPOSER_MAX_LENGTH - value.length;
  const showCounter = remaining <= COUNTER_VISIBLE_FROM;
  const canSend = value.trim().length > 0 && !streaming && !disabled;

  return (
    <form
      className="shrink-0 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <label htmlFor={textareaId} className="sr-only">
        Message Transform AI
      </label>
      {/* `relative` is the positioning context for the mention popup, which is
          anchored above this card. */}
      <div className="relative rounded-2xl border border-outline-strong bg-surface-variant/40 p-1.5 shadow-lg shadow-black/20 transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/25">
        {attachments.length > 0 && (
          <div className="px-0.5 pb-2">
            <AttachmentChips
              attachments={attachments}
              onRemove={onRemoveAttachment}
              collapsible={false}
            />
          </div>
        )}
        <div className="flex items-end gap-1.5">
          {onAttach && (
            <AttachControl
              attachments={attachments}
              onAttach={onAttach}
              disabled={disabled || streaming}
              maxAttachments={maxAttachments}
            />
          )}
          <textarea
            id={textareaId}
            ref={textareaRef}
            rows={1}
            value={value}
            disabled={disabled}
            maxLength={COMPOSER_MAX_LENGTH}
            onChange={(e) => {
              onChange(e.target.value.slice(0, COMPOSER_MAX_LENGTH));
              // Read the caret from the event's own element: `value` has not
              // been re-rendered yet, so the ref still holds the previous text.
              setMention(
                mentionQueryAt(e.target.value.slice(0, COMPOSER_MAX_LENGTH), e.target.selectionStart ?? 0),
              );
            }}
            onKeyDown={handleKeyDown}
            onKeyUp={refreshMention}
            onClick={refreshMention}
            // A genuine focus loss closes the list. Choosing a row cannot cause
            // one: the options cancel `mousedown` so the caret never leaves.
            onBlur={() => setMention(null)}
            placeholder={disabled ? "The assistant is unavailable." : "Ask about your files…"}
            aria-describedby={showCounter ? `${textareaId}-count` : undefined}
            // The ARIA combobox pattern: focus stays in the textarea (so the
            // draft is never interrupted) and the highlighted option is
            // announced from here via `aria-activedescendant`.
            role="combobox"
            aria-expanded={pickerVisible}
            aria-controls={pickerVisible ? listboxId : undefined}
            aria-autocomplete="list"
            aria-activedescendant={
              pickerVisible && hasMentionResults
                ? `${listboxId}-option-${mentionState.activeIndex}`
                : undefined
            }
            className="max-h-40 min-h-9 w-full resize-none bg-transparent px-1 py-1.5 text-sm text-on-background placeholder:text-muted focus:outline-none disabled:cursor-not-allowed"
          />
          {streaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-outline-strong px-3 text-sm font-semibold text-on-background transition-colors hover:bg-surface-variant pointer-coarse:min-h-11"
            >
              <Stop size={15} weight="fill" /> Stop
            </button>
          ) : (
            <motion.button
              type="submit"
              disabled={!canSend}
              aria-label="Send message"
              whileHover={reduce || !canSend ? undefined : { scale: 1.06 }}
              whileTap={reduce || !canSend ? undefined : { scale: 0.92 }}
              transition={{ type: "spring", stiffness: 420, damping: 24 }}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 pointer-coarse:min-h-11 pointer-coarse:min-w-11"
            >
              <PaperPlaneRight size={16} weight="fill" />
            </motion.button>
          )}
        </div>

        {pickerVisible && (
          <DocumentMentionPicker
            id={listboxId}
            query={mention?.query ?? ""}
            results={mentionState.results}
            loading={mentionState.loading}
            error={mentionState.error}
            activeIndex={mentionState.activeIndex}
            onSelect={chooseMentionedFile}
            onHover={mentionState.setActiveIndex}
          />
        )}
      </div>
      <div className="flex items-center justify-between gap-3 px-2 pt-1.5">
        <p className="text-[11px] text-muted">
          Enter to send, Shift+Enter for a new line. Type <kbd className="font-sans font-semibold">@</kbd> to
          reference a file.
        </p>
        {showCounter && (
          <p
            id={`${textareaId}-count`}
            className={`font-mono text-[11px] ${remaining <= 0 ? "text-error" : "text-muted"}`}
          >
            {remaining} left
          </p>
        )}
      </div>
    </form>
  );
}
