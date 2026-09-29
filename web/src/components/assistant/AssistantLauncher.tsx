import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import { Sparkle, X } from "@phosphor-icons/react";
import type { AssistantAttachment } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { assistantErrorCopy } from "@/lib/assistantChat";
import { MAX_ASSISTANT_ATTACHMENTS, attachAssistantFile, removeAssistantAttachment } from "@/lib/assistantAttachments";
import { shouldShowLauncher } from "@/lib/launcherVisibility";
import { miniAssistantStore } from "@/lib/miniAssistantStore";
import { useAssistantChat } from "@/lib/useAssistantChat";
import { useNarrowViewport } from "@/lib/useMediaQuery";

/**
 * The mini chat panel, loaded on first open.
 *
 * `AssistantLauncher` is mounted in `AppShell`, so it is in the **entry**
 * bundle for every authed route. Importing `MiniAssistant` statically dragged
 * the whole chat UI — composer, attach dialog, message list, rich text, the
 * artifact chips and the file-preview modal — into that entry chunk even for a
 * user who never opens the assistant. The launcher itself (button + store +
 * streaming hook) stays eager so the affordance is instant; only the panel is
 * deferred, and it is `Suspense`-wrapped below so the first open swaps from a
 * shell with no layout shift.
 */
const MiniAssistant = lazy(() =>
  import("@/components/assistant/MiniAssistant").then((m) => ({ default: m.MiniAssistant })),
);

/** The panel shell shown while the lazy chunk loads (first open only). */
function PanelFallback() {
  return (
    <div
      aria-hidden
      className="h-full w-full rounded-2xl border border-outline bg-surface shadow-2xl"
    />
  );
}

/**
 * The floating Transform AI launcher and its mini chat.
 *
 * Desktop/tablet only (`md+`). Both the CSS (`hidden md:flex`) and
 * `useNarrowViewport()` gate on the same breakpoint so the button can never be
 * visible-but-inert or hidden-but-stateful. It is also hidden on
 * `/app/assistant`, where the whole page already *is* the assistant; both
 * conditions feed `shouldShowLauncher` (see `lib/launcherVisibility`). The mini
 * chat reuses the page's streaming hook against a module-level store, so its
 * thread survives the panel closing and the route changing, and it sends
 * `context: location.pathname` with every turn so it can answer "what's on this
 * page?".
 *
 * Both the button and the panel are `motion` elements sitting directly inside
 * one `AnimatePresence`, so entering/leaving the assistant page (or crossing the
 * breakpoint) fades them out and in rather than popping. See the inline notes
 * for why a custom component in that slot breaks the exit.
 *
 * The whole thing is portalled to `document.body`: the app's page wrapper
 * carries a transform during its enter animation, which would otherwise become
 * the containing block for these `position: fixed` elements (see
 * `UploadsDock.tsx`).
 */
export function AssistantLauncher() {
  const narrow = useNarrowViewport();
  const location = useLocation();
  const { user, api: client } = useAuth();
  const { error: toastError } = useToast();
  const reduce = useReducedMotion();

  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<AssistantAttachment[]>([]);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const { chat, send, stop, regenerate, retryLastTurn, editMessage, newChat } = useAssistantChat({
    store: miniAssistantStore,
    onError: (code, message) => {
      const copy = assistantErrorCopy(code, message);
      // A gated tier is already explained by the panel's own empty state; a
      // toast would be redundant. Everything else is surfaced as a toast.
      if (copy.kind !== "tier") toastError(copy.toast);
    },
    onAttachmentNotFound: (prompt) => {
      // The composer was cleared when the turn was sent, so restoring the text
      // is all that is needed — there is no stale chip list to strip.
      setInput(prompt);
      setAttachments([]);
      toastError("That file is no longer available");
    },
    onAttachmentLimitExceeded: (prompt, files, message) => {
      // A last-resort net. The cap below is fetched from `/status`, so this only
      // fires if the plan changed mid-session or the status call failed — put the
      // whole turn back so the user can drop a file and resend, and surface the
      // server's own message (it already names the plan's number).
      setInput(prompt);
      setAttachments(files);
      toastError(message);
    },
  });

  // Reset the stored thread when the signed-in identity changes, so one
  // account's conversation can never appear in another's panel.
  const previousUser = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    const id = user?.id ?? null;
    const previous = previousUser.current;
    previousUser.current = id;
    if (previous === undefined || previous === id) return;
    newChat();
    setInput("");
    setAttachments([]);
    setOpen(false);
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps -- identity change only

  // The caller's attachment allowance, from `/status`. Fetched lazily (only once
  // the panel is opened) because the launcher is mounted on EVERY authed route
  // and must not add a request to each page load. Until it arrives — and if the
  // call fails — the shared fallback applies, which the 403 handler above backs
  // up. Without this the mini chat enforced the fallback (5) and a FREE caller
  // could build a turn the server would refuse.
  const [maxAttachments, setMaxAttachments] = useState(MAX_ASSISTANT_ATTACHMENTS);
  const fetchedCap = useRef(false);
  useEffect(() => {
    if (!open || fetchedCap.current) return;
    fetchedCap.current = true;
    let active = true;
    client
      .assistantStatus()
      .then((next) => {
        if (active && typeof next.max_attachments === "number" && next.max_attachments > 0) {
          setMaxAttachments(next.max_attachments);
        }
      })
      .catch(() => {
        // Non-fatal: the fallback cap plus the 403 recovery still work.
      });
    return () => {
      active = false;
    };
  }, [open, client]);

  // Close on an outside pointer press; a press on the launcher toggles instead.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function outgoing(): AssistantAttachment[] {
    return attachments.slice(0, maxAttachments);
  }

  function handleSubmit() {
    const text = input.trim();
    if (!text || chat.streaming) return;
    const files = outgoing();
    setInput("");
    setAttachments([]);
    send(text, { attachments: files, context: location.pathname });
  }

  function handleQuickSend(prompt: string) {
    if (chat.streaming) return;
    const files = outgoing();
    setAttachments([]);
    send(prompt, { attachments: files, context: location.pathname });
  }

  function handleNew() {
    newChat();
    setInput("");
    setAttachments([]);
  }

  // Hooks have all run; only now may we skip rendering. Without a DOM there is
  // nothing to portal into, and `renderToString` (AppShell's tests) must not
  // try.
  //
  // We deliberately do NOT return early for the assistant route or a narrow
  // viewport: both feed `showLauncher` below so `AnimatePresence` can play the
  // *exit* fade instead of the button vanishing instantly. The `hidden md:flex`
  // classes stay as the CSS half of the narrow-viewport decision, kept in
  // lockstep with `useNarrowViewport()`.
  if (typeof document === "undefined") return null;

  const showLauncher = shouldShowLauncher({ narrow, pathname: location.pathname });

  return createPortal(
    <AnimatePresence>
      {showLauncher && (
        // The direct child of `AnimatePresence` is this `motion.button` itself
        // (not a wrapper component), so the exit animation actually runs and the
        // button is removed on the assistant route. A custom component in this
        // slot would never complete its exit — the same trap that left the
        // panel behind (and is recorded for `FormatPicker`).
        <motion.button
          key="launcher-button"
          ref={buttonRef}
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-label={open ? "Close Transform AI" : "Ask Transform AI"}
          aria-haspopup="dialog"
          aria-expanded={open}
          initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.9 }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.9 }}
          whileHover={reduce ? undefined : { scale: 1.06, y: -2 }}
          whileTap={reduce ? undefined : { scale: 0.94 }}
          transition={{ type: "spring", stiffness: 400, damping: 22 }}
          className="fixed right-4 bottom-[calc(1rem_+_env(safe-area-inset-bottom))] z-40 hidden h-11 w-11 items-center justify-center rounded-full border border-outline-strong bg-primary text-on-primary shadow-xl shadow-black/30 md:flex"
        >
          {open ? <X size={18} weight="bold" /> : <Sparkle size={18} weight="fill" />}
        </motion.button>
      )}

      {showLauncher && open && (
        // The panel's animation lives on THIS `motion.div`, the direct child of
        // `AnimatePresence`; `MiniAssistant` renders only the header/list/
        // composer inside it. Previously `MiniAssistant` owned the `motion.div`,
        // so `AnimatePresence`'s direct child was a custom component — its exit
        // never completed and the panel stayed in the DOM as an invisible yet
        // hit-testable overlay swallowing clicks in the bottom-right of every
        // page. `panelRef` now points at the inner container, so the
        // outside-click check and focus query still measure the real panel.
        // Under reduced motion only opacity animates (no scale/translate).
        <motion.div
          key="launcher-panel"
          initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.94, y: 12 }}
          animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.94, y: 12 }}
          transition={{ type: "spring", stiffness: 420, damping: 32 }}
          style={{ transformOrigin: "bottom right" }}
          // Anchored above the launcher. With the button now 44px (`h-11`) top
          // at 1rem + 2.75rem, this `4.5rem` offset leaves a clean 0.75rem gap.
          // `hidden md:flex` agrees with the launcher's `useNarrowViewport`
          // gate, so the two cannot disagree about the panel.
          className="fixed right-4 bottom-[calc(4.5rem_+_env(safe-area-inset-bottom))] z-50 hidden h-[30rem] max-h-[calc(100dvh-6rem)] w-[24rem] max-w-[calc(100vw-2rem)] md:flex"
        >
          <Suspense fallback={<PanelFallback />}>
            <MiniAssistant
              panelRef={panelRef}
              triggerRef={buttonRef}
              chat={chat}
              onClose={() => setOpen(false)}
              onNew={handleNew}
              onStop={stop}
              onRetry={(messageId) => regenerate(messageId, location.pathname)}
              onRetryLast={retryLastTurn}
              onEdit={(messageId, text, files) =>
                void editMessage(messageId, text, files, location.pathname)
              }
              maxAttachments={maxAttachments}
              input={input}
              onInputChange={setInput}
              attachments={attachments}
              onAttach={(attachment) =>
                setAttachments((prev) => attachAssistantFile(prev, attachment).attachments)
              }
              onRemoveAttachment={(id) =>
                setAttachments((prev) => removeAssistantAttachment(prev, id))
              }
              onSubmit={handleSubmit}
              onQuickSend={handleQuickSend}
            />
          </Suspense>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
