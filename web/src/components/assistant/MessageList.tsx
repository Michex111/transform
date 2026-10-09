import { useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowDown } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import { Skeleton } from "@/components/ui";
import type { AssistantChatMessage, AssistantToolActivity } from "@/lib/assistantChat";
import { isNearBottom } from "@/lib/scrollFollow";
import { MessageBubble } from "@/components/assistant/MessageBubble";
import { ToolActivity } from "@/components/assistant/ToolActivity";

/**
 * The transcript.
 *
 * `role="log"` + `aria-live="polite"` is the standard chat pattern: additions
 * (the streamed text, the tool activity row) are announced as they land, while
 * the history already on screen is not re-read on every render.
 */
export function MessageList({
  messages,
  tools,
  stage,
  streaming,
  loading = false,
  onRetry,
  onEdit,
  dense = false,
}: {
  messages: AssistantChatMessage[];
  tools: AssistantToolActivity[];
  stage: string | null;
  streaming: boolean;
  loading?: boolean;
  onRetry?: (messageId: string) => void;
  /**
   * Save an edited user message. Optional so a surface that has not wired the
   * edit flow (or the mini chat, until it is) simply shows no Edit affordance.
   */
  onEdit?: (messageId: string, text: string, attachments: AssistantAttachment[]) => void;
  /** Tighter spacing/padding for the floating mini chat. */
  dense?: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  // Whether the transcript is following its newest content. The scroll position
  // belongs to the reader: while they are reading an earlier answer, a streamed
  // delta must not drag them back down. `true` initially because the newest
  // message is what someone opening a conversation wants to see.
  const [pinned, setPinned] = useState(true);

  // The latest handlers, read through a ref so the per-message callbacks below
  // can be referentially stable. Callers pass a fresh closure every render (they
  // must — it closes over the current turn), and if we forwarded those directly
  // every `MessageBubble`'s props changed on every streamed delta, which makes
  // its `React.memo` a no-op and re-renders the whole (potentially long)
  // transcript for each token. The effect (not a render-time write) keeps the
  // ref current without mutating during render.
  const handlersRef = useRef({ onRetry, onEdit });
  useEffect(() => {
    handlersRef.current = { onRetry, onEdit };
  });
  const stableRetry = useCallback(
    (messageId: string) => handlersRef.current.onRetry?.(messageId),
    [],
  );
  const stableEdit = useCallback(
    (messageId: string, text: string, attachments: AssistantAttachment[]) =>
      handlersRef.current.onEdit?.(messageId, text, attachments),
    [],
  );
  // Presence (not identity) decides whether the affordance renders, so pass
  // `undefined` — itself stable — when a surface has no handler.
  const bubbleRetry = onRetry ? stableRetry : undefined;
  const bubbleEdit = onEdit ? stableEdit : undefined;

  // Which transcript is on screen, by its first message. Reopening a different
  // conversation (or starting a new one) has to begin pinned to its newest
  // message, however far up the previous transcript was left.
  const firstMessageId = messages[0]?.id ?? null;
  const transcriptRef = useRef(firstMessageId);
  useEffect(() => {
    if (transcriptRef.current !== firstMessageId) {
      transcriptRef.current = firstMessageId;
      setPinned(true);
    }
  }, [firstMessageId]);

  // Follow the stream — but only while the reader is already at the bottom.
  // `block: "end"` scrolls the nearest scrollable ancestor, so the page itself
  // never jumps.
  useEffect(() => {
    if (!pinned) return;
    // Guarded because `scrollIntoView` is absent in non-DOM renderers.
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, streaming, pinned]);

  function handleScroll() {
    const element = scrollRef.current;
    if (!element) return;
    setPinned(isNearBottom(element));
  }

  function jumpToLatest() {
    setPinned(true);
    endRef.current?.scrollIntoView?.({
      block: "end",
      behavior: reduce ? "auto" : "smooth",
    });
  }

  if (loading) {
    return (
      <div className="min-h-0 flex-1 space-y-4 overflow-hidden p-4">
        {[0, 1, 2].map((row) => (
          <div key={row} className={row % 2 ? "flex justify-end" : "flex justify-start"}>
            <Skeleton className={`h-14 ${row % 2 ? "w-2/5" : "w-3/5"} rounded-2xl`} />
          </div>
        ))}
      </div>
    );
  }

  return (
    // The scroller and the "jump to latest" control are siblings, not nested:
    // the control has to stay put while the transcript moves under it.
    <div className="relative min-h-0 flex-1">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className={`h-full overflow-y-auto ${dense ? "space-y-3 p-3" : "space-y-4 p-4"}`}
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-label="Conversation"
      >
        {messages.map((message, index) => (
          // A quiet entrance, staggered a touch so a loaded conversation does not
          // pop in all at once. Dropped entirely under reduced motion.
          <motion.div
            key={message.id}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={reduce ? undefined : { opacity: 1, y: 0 }}
            transition={{
              duration: 0.22,
              ease: [0.22, 1, 0.36, 1],
              delay: reduce ? 0 : Math.min(index, 6) * 0.02,
            }}
          >
            <MessageBubble message={message} onRetry={bubbleRetry} onEdit={bubbleEdit} />
          </motion.div>
        ))}
        <ToolActivity tools={tools} stage={stage} streaming={streaming} />
        <div ref={endRef} />
      </div>

      {/* Only while there is newer content the reader has scrolled away from. */}
      {!pinned && messages.length > 0 && (
        <button
          type="button"
          onClick={jumpToLatest}
          className="absolute bottom-3 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-outline-strong bg-surface px-3 py-1.5 text-xs font-medium text-on-background shadow-lg shadow-black/30 transition-colors hover:bg-surface-variant pointer-coarse:min-h-11"
        >
          <ArrowDown size={13} weight="bold" aria-hidden />
          Jump to latest
        </button>
      )}
    </div>
  );
}
