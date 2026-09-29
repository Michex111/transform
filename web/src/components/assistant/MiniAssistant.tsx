import { useEffect, useRef, useState, type RefObject } from "react";
import { Plus, Sparkle, X } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import { Composer } from "@/components/assistant/Composer";
import { MessageList } from "@/components/assistant/MessageList";
import { assistantErrorCopy, type AssistantChatState } from "@/lib/assistantChat";
import { pickMiniSuggestions } from "@/lib/suggestedPrompts";

/**
 * The floating mini chat's panel.
 *
 * A non-blocking companion, not a modal: `aria-modal="false"`, no focus trap,
 * and Escape closes it. It reuses `MessageList`/`MessageBubble` and the
 * `Composer`, so copy/retry, tool steps and attachments all work exactly as
 * they do on the full page — the only differences are the compact sizing and
 * the page-aware `context` sent with every turn.
 *
 * The transcript lives in the launcher's hook (backed by a module-level store),
 * so closing this panel does not throw the conversation away.
 *
 * This component deliberately renders **no motion and no positioning**: the
 * launcher wraps it in the animated, `position: fixed` `motion.div` that
 * `AnimatePresence` can track as a direct child. Here we only render the
 * header/list/composer that fill that wrapper.
 */
export function MiniAssistant({
  panelRef,
  triggerRef,
  chat,
  onClose,
  onNew,
  onStop,
  onRetry,
  onRetryLast,
  onEdit,
  input,
  onInputChange,
  attachments,
  onAttach,
  onRemoveAttachment,
  maxAttachments,
  onSubmit,
  onQuickSend,
}: {
  panelRef: RefObject<HTMLDivElement>;
  triggerRef: RefObject<HTMLButtonElement>;
  chat: AssistantChatState;
  onClose: () => void;
  onNew: () => void;
  onStop: () => void;
  onRetry: (messageId: string) => void;
  onRetryLast: () => void;
  /** Save an edited user message (same contract as the full page's editor). */
  onEdit: (messageId: string, text: string, attachments: AssistantAttachment[]) => void;
  input: string;
  onInputChange: (value: string) => void;
  attachments: AssistantAttachment[];
  onAttach: (attachment: AssistantAttachment) => void;
  onRemoveAttachment: (id: string) => void;
  /** The caller's plan attachment cap, so the mini chat caps like the page. */
  maxAttachments: number;
  onSubmit: () => void;
  onQuickSend: (prompt: string) => void;
}) {
  const [seed] = useState(() => Math.floor(Math.random() * 0xffffffff));
  // Stable so the Escape effect below can depend on the panel's lifetime alone.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  const suggestions = pickMiniSuggestions(seed, 3);
  const isEmpty = chat.messages.length === 0 && !chat.streaming;
  const error = chat.error;
  const errorCopy = error ? assistantErrorCopy(error.code, error.message) : null;

  // Move focus into the panel on open and return it to the launcher on close.
  // The cleanup is the close path: this component unmounts whenever the panel
  // closes, so a keyboard user is put back where they were rather than on
  // `<body>`.
  useEffect(() => {
    const trigger = triggerRef.current;
    const timer = window.setTimeout(() => {
      panelRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    }, 30);
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      // A nested modal (the attach dialog) owns Escape while it is open —
      // without this, one Escape would dismiss the dialog *and* the panel.
      const target = e.target as Element | null;
      if (target?.closest?.('[aria-modal="true"]')) return;
      onCloseRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      trigger?.focus();
    };
  }, [panelRef, triggerRef]);

  function quickSend(prompt: string) {
    if (chat.streaming) return;
    onQuickSend(prompt);
  }

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-modal="false"
      aria-label="Transform AI"
      tabIndex={-1}
      // This element is the *inner* container: the launcher owns the animated,
      // `position: fixed` `motion.div` wrapper so that `AnimatePresence` sees a
      // `motion` element as its direct child and can actually run the exit. The
      // wrapper sizes itself; this fills it and carries the visible shell.
      //
      // `panelRef` points here (not at the wrapper), so the outside-click check
      // and the focus query still measure the real panel. `min-w-0` keeps a
      // wide transcript from widening the panel past its `w-[24rem]` wrapper.
      className="flex h-full w-full min-w-0 flex-col overflow-hidden rounded-2xl border border-outline bg-surface shadow-2xl focus:outline-none"
    >
      <header className="flex shrink-0 items-center gap-2 border-b border-outline px-3 py-2.5">
        <Sparkle size={16} weight="fill" className="shrink-0 text-primary" aria-hidden />
        <h2 className="min-w-0 flex-1 truncate font-display text-sm font-semibold">Transform AI</h2>
        <button
          type="button"
          onClick={onNew}
          aria-label="New chat"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:min-h-11"
        >
          <Plus size={14} weight="bold" /> New
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close Transform AI"
          className="rounded-md p-1.5 text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:min-w-11"
        >
          <X size={16} />
        </button>
      </header>

      {isEmpty ? (
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          <p className="text-xs text-muted">
            Ask about this page, your files, or how many credits you have left.
          </p>
          <ul className="space-y-2">
            {suggestions.map((prompt) => (
              <li key={prompt}>
                <button
                  type="button"
                  onClick={() => quickSend(prompt)}
                  className="flex w-full items-center gap-2 rounded-lg border border-outline bg-surface-variant/40 px-3 py-2.5 text-left text-sm text-on-background transition-colors hover:border-primary/60 hover:bg-surface-variant pointer-coarse:min-h-11"
                >
                  <Sparkle size={14} weight="fill" className="shrink-0 text-primary" aria-hidden />
                  <span className="min-w-0">{prompt}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <MessageList
          messages={chat.messages}
          tools={chat.tools}
          stage={chat.stage}
          streaming={chat.streaming}
          onRetry={onRetry}
          onEdit={onEdit}
          dense
        />
      )}

      {error && errorCopy && errorCopy.kind !== "tier" && (
        <div
          role="alert"
          className="flex shrink-0 flex-wrap items-center gap-2 border-t border-outline bg-error/5 px-3 py-2"
        >
          <p className="min-w-0 flex-1 text-xs text-on-background">{errorCopy.detail}</p>
          <button
            type="button"
            onClick={onRetryLast}
            className="rounded-md px-2 py-1 text-xs font-semibold text-primary transition-colors hover:bg-surface-variant pointer-coarse:min-h-11"
          >
            Try again
          </button>
        </div>
      )}

      <Composer
        value={input}
        onChange={onInputChange}
        onSend={onSubmit}
        onStop={onStop}
        streaming={chat.streaming}
        attachments={attachments}
        onAttach={onAttach}
        onRemoveAttachment={onRemoveAttachment}
        maxAttachments={maxAttachments}
      />
    </div>
  );
}
