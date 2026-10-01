import { useCallback, useEffect, useRef } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { AssistantAttachment } from "@/api/types";
import { Skeleton } from "@/components/ui";
import type { AssistantChatMessage, AssistantToolActivity } from "@/lib/assistantChat";
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
  const reduce = useReducedMotion();

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

  // Follow the stream: keep the newest text in view without fighting a user who
  // has scrolled up. `block: "end"` only scrolls the nearest scrollable
  // ancestor, so the page itself does not jump.
  useEffect(() => {
    // Guarded because `scrollIntoView` is absent in non-DOM renderers.
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, streaming]);

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
    <div
      className={`min-h-0 flex-1 overflow-y-auto ${dense ? "space-y-3 p-3" : "space-y-4 p-4"}`}
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
  );
}
