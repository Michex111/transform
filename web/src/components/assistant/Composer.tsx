import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { PaperPlaneRight, Stop } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import { AttachmentChips } from "@/components/assistant/AttachmentChips";
import { AttachControl } from "@/components/assistant/AttachControl";

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

  // Auto-grow. Height is reset to `auto` before reading `scrollHeight`, because
  // a textarea only ever reports the height it currently has plus overflow — it
  // never shrinks on its own once it has grown.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
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
      // The bottom padding folds in the home-indicator inset: on an iPhone in
      // landscape (or a device with a home indicator in portrait) the Send
      // button would otherwise sit inside the gesture area. `max()` keeps the
      // existing 0.75rem on every device where the inset is 0.
      className="shrink-0 p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <label htmlFor={textareaId} className="sr-only">
        Message Transform AI
      </label>
      <div className="rounded-2xl border border-outline-strong bg-surface-variant/40 p-1.5 shadow-lg shadow-black/20 transition-colors focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/25">
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
            onChange={(e) => onChange(e.target.value.slice(0, COMPOSER_MAX_LENGTH))}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? "The assistant is unavailable." : "Ask about your files…"}
            aria-describedby={showCounter ? `${textareaId}-count` : undefined}
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
      </div>
      <div className="flex items-center justify-between gap-3 px-2 pt-1.5">
        <p className="text-[11px] text-muted">Enter to send, Shift+Enter for a new line.</p>
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
