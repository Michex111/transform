import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Info, List, Paperclip, Sparkle, X } from "@phosphor-icons/react";
import type { AssistantAttachment, AssistantConversation, AssistantStatus } from "@/api/types";
import { ASSISTANT_NOT_AVAILABLE_FOR_TIER } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, PageLoader } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { Composer } from "@/components/assistant/Composer";
import { ConversationList } from "@/components/assistant/ConversationList";
import { MessageList } from "@/components/assistant/MessageList";
import { SuggestedPrompts } from "@/components/assistant/SuggestedPrompts";
import { assistantErrorCopy } from "@/lib/assistantChat";
import { usageLabel, usageTitle } from "@/lib/assistantUsage";
import {
  MAX_ASSISTANT_ATTACHMENTS,
  attachAssistantFile,
  removeAssistantAttachment,
  withoutAttachmentIds,
} from "@/lib/assistantAttachments";
import { createAssistantChatStore, type AssistantChatStore } from "@/lib/assistantChatStore";
import { fileNameExtension } from "@/lib/format";
import { useAssistantChat } from "@/lib/useAssistantChat";

/** Navigation state the Files page uses to hand a file question to the assistant. */
interface AssistantPrefill {
  prompt?: string;
  fileId?: string;
  fileName?: string;
}

/**
 * A fresh seed for the suggestion pool.
 *
 * Seeding per session (and per new chat) is what stops every empty state from
 * showing the same four examples. It is only a seed, so a weak source is fine.
 */
function newSuggestSeed(): number {
  return Math.floor(Math.random() * 0xffffffff);
}

export function AssistantPage() {
  const { api: client } = useAuth();
  const { error: toastError } = useToast();
  const location = useLocation();
  const reduce = useReducedMotion();

  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  // The plan's attachment cap, from `/status` when the API is new enough. The
  // normaliser already substitutes MAX_ASSISTANT_ATTACHMENTS for an absent
  // value; the `??` keeps that same default if a status object ever lacks it.
  const attachmentCap = status?.max_attachments ?? MAX_ASSISTANT_ATTACHMENTS;
  // Set when the API says this account's plan has no assistant, so the page
  // shows the explanation instead of a composer that would only ever fail.
  const [tierBlocked, setTierBlocked] = useState(false);

  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<AssistantAttachment[]>([]);
  const [context, setContext] = useState<{ fileId: string; fileName?: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AssistantConversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Seed for the empty-state suggestions; reseeded on mount (the initializer)
  // and on every `newChat()` so a fresh chat never repeats the last set.
  const [suggestSeed, setSuggestSeed] = useState(newSuggestSeed);

  // The transcript lives in an external store so the shared chat hook owns it
  // (the same hook backs the floating mini chat, which keeps its thread in a
  // module-level store).
  const storeRef = useRef<AssistantChatStore | null>(null);
  if (!storeRef.current) storeRef.current = createAssistantChatStore();
  const store = storeRef.current;

  const drawerRef = useRef<HTMLElement>(null);
  const drawerTriggerRef = useRef<HTMLButtonElement>(null);

  // ---- Status ----
  useEffect(() => {
    let cancelled = false;
    client
      .assistantStatus()
      .then((next) => {
        if (!cancelled) setStatus(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // A gated tier is a definite answer, not a network blip: show the panel.
        // Anything else stays optimistic — the chat call itself will report the
        // real problem, and blocking the page on a failed probe would be worse.
        if ((err as { code?: string }).code === ASSISTANT_NOT_AVAILABLE_FOR_TIER) {
          setTierBlocked(true);
        }
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  // ---- Conversation list ----
  const refreshConversations = useCallback(async () => {
    try {
      const res = await client.assistantConversations();
      setConversations(res.conversations);
    } catch {
      // Non-fatal: the rail stays empty and a new chat still works.
    } finally {
      setConversationsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    void refreshConversations();
  }, [refreshConversations]);

  // ---- The streaming chat engine (shared with the mini chat) ----
  const {
    chat,
    historyLoading,
    send: sendTurn,
    stop,
    regenerate,
    retryLastTurn,
    editMessage,
    newChat: resetChat,
    loadConversation,
  } = useAssistantChat({
    store,
    onTurnDone: (conversationId) => {
      setActiveId(conversationId);
      void refreshConversations();
    },
    onError: (code, message) => {
      const copy = assistantErrorCopy(code, message);
      if (copy.kind === "tier") {
        setTierBlocked(true);
        return;
      }
      toastError(copy.toast);
    },
    onAttachmentNotFound: (prompt, gone) => {
      // Keep the typed message so the user can resend it without the file that
      // no longer exists; the failed turn is already gone from the transcript.
      setInput(prompt);
      setAttachments((prev) => withoutAttachmentIds(prev, gone.map((entry) => entry.id)));
      setContext(null);
      toastError("That file is no longer available");
    },
    onAttachmentLimitExceeded: (prompt, files, message) => {
      // Nothing is wrong with the files — there are just too many for the plan.
      // Put the whole turn back (message + every file) so the user can drop one
      // and resend, and surface the server's message, which names the plan's
      // number. The context file, if any, is inside `files` already.
      setInput(prompt);
      setAttachments(files);
      toastError(message);
    },
  });

  // ---- Hand-off from the Files page ----
  const prefill = (location.state as AssistantPrefill | null) ?? null;
  const appliedPrefill = useRef(false);
  useEffect(() => {
    if (appliedPrefill.current || !prefill) return;
    appliedPrefill.current = true;
    if (prefill.prompt) setInput(prefill.prompt);
    if (prefill.fileId) setContext({ fileId: prefill.fileId, fileName: prefill.fileName });
  }, [prefill]);

  // ---- Mobile drawer focus + Escape ----
  useEffect(() => {
    if (!drawerOpen) return;
    const trigger = drawerTriggerRef.current;
    const timer = window.setTimeout(() => drawerRef.current?.focus(), 30);
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setDrawerOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKey);
      // The panel unmounts on close, so focus has to be returned explicitly or
      // it falls to `<body>` and the next Tab restarts at the top of the page.
      trigger?.focus();
    };
  }, [drawerOpen]);

  /** The composer's attachments plus the file handed over from the Files page. */
  function outgoingAttachments(): AssistantAttachment[] {
    const list = [...attachments];
    if (context?.fileId && !list.some((entry) => entry.id === context.fileId)) {
      const extension = context.fileName ? fileNameExtension(context.fileName) : "";
      list.push({
        id: context.fileId,
        name: context.fileName ?? "Attached file",
        ...(extension ? { extension } : {}),
      });
    }
    return list.slice(0, attachmentCap);
  }

  function send(raw?: string) {
    const text = (raw ?? input).trim();
    if (!text || chat.streaming || tierBlocked) return;
    const outgoing = outgoingAttachments();
    setInput("");
    setAttachments([]);
    setContext(null);
    sendTurn(text, { attachments: outgoing, context: location.pathname });
  }

  function newChat() {
    resetChat();
    setActiveId(null);
    setInput("");
    setAttachments([]);
    setContext(null);
    setDrawerOpen(false);
    setSuggestSeed(newSuggestSeed());
  }

  async function openConversation(id: string) {
    setDrawerOpen(false);
    if (id === activeId) return;
    setInput("");
    setAttachments([]);
    setActiveId(id);
    try {
      await loadConversation(id, async () => (await client.assistantConversation(id)).messages);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not load that conversation");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await client.assistantDeleteConversation(deleteTarget.id);
      setConversations((prev) => prev.filter((entry) => entry.id !== deleteTarget.id));
      if (activeId === deleteTarget.id) newChat();
      setDeleteTarget(null);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "Could not delete that conversation");
    } finally {
      setDeleting(false);
    }
  }

  function attachFile(attachment: AssistantAttachment) {
    setAttachments((prev) => attachAssistantFile(prev, attachment, attachmentCap).attachments);
  }

  function removeAttachment(id: string) {
    setAttachments((prev) => removeAssistantAttachment(prev, id));
  }

  /** Regenerate the answer with `messageId` (the Retry control on a bubble). */
  function handleRegenerate(messageId: string) {
    regenerate(messageId, location.pathname);
  }

  /** The last thing the user asked, for the inline retry after a failure. */
  const lastUserMessage = (() => {
    for (let i = chat.messages.length - 1; i >= 0; i--) {
      if (chat.messages[i].role === "user") return chat.messages[i].content;
    }
    return null;
  })();

  const assistantDisabled = tierBlocked || status?.enabled === false;
  const usage = usageLabel(status);
  const usageFull = usageTitle(status);
  const activeConversation = conversations.find((entry) => entry.id === activeId) ?? null;
  const isEmpty = chat.messages.length === 0 && !chat.streaming && !historyLoading;

  return (
    // Fill the shell's content area exactly, so the gutter below the card
    // matches the gutter above it.
    //
    // The subtracted amount is the chrome `main` is given, and it is now
    // derived rather than guessed:
    //
    //   below `lg`: header `h-14` (3.5rem) + `main`'s `py-5` (2.5rem) +
    //               bottom nav (~4rem) = 10rem
    //   at `lg`:    the header and the bottom nav are both `lg:hidden`, so
    //               only `main`'s `py-5` remains = 2.5rem
    //
    // Both previous values were wrong in exactly one of those two directions.
    // The old `lg` figure still reserved room for the phone header, which
    // `lg:hidden` removes, so the card sat 48px high on desktop; below `lg` it
    // assumed a 64px bottom nav where the real one measures 63.17px, leaving
    // 9px. `env(safe-area-inset-bottom)` is subtracted too, because the shell
    // adds it to the nav as padding, which makes the nav taller on iOS.
    //
    // These values must track `AppShell` (`main`'s padding, the header and the
    // bottom nav). That is noted there as well, from the other side.
    <div className="mx-auto flex h-[calc(100dvh-10rem-env(safe-area-inset-bottom))] min-h-[24rem] max-w-5xl gap-4 lg:h-[calc(100dvh-2.5rem)]">
      {/* Desktop rail */}
      <aside className="hidden w-64 shrink-0 overflow-hidden rounded-xl border border-outline bg-surface lg:flex">
        <ConversationList
          conversations={conversations}
          activeId={activeId}
          loading={conversationsLoading}
          onSelect={(id) => void openConversation(id)}
          onNew={newChat}
          onRequestDelete={setDeleteTarget}
        />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-xl border border-outline bg-surface">
        <header className="flex shrink-0 items-center gap-3 border-b border-outline px-3 py-3 sm:px-4">
          <button
            ref={drawerTriggerRef}
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="Open conversations"
            aria-expanded={drawerOpen}
            aria-haspopup="dialog"
            className="-ml-1 shrink-0 rounded-lg p-2.5 text-muted transition-colors hover:bg-surface-variant hover:text-on-background lg:hidden pointer-coarse:min-h-11 pointer-coarse:min-w-11"
          >
            <List size={18} />
          </button>
          <Sparkle size={18} weight="fill" className="shrink-0 text-primary" aria-hidden />
          <div className="min-w-0 flex-1">
            <h1 className="font-display text-base font-semibold leading-tight">Transform AI</h1>
            <p className="truncate text-xs text-muted">
              {activeConversation?.title || "Ask about your files, or get format advice."}
            </p>
          </div>
          {status?.backend === "echo" && <EchoBadge />}
          {usage && (
            <span
              className="hidden shrink-0 items-center rounded-full border border-outline bg-surface-variant/60 px-2.5 py-1 text-[11px] font-semibold text-muted sm:inline-flex"
              title={usageFull ?? usage}
            >
              {usage}
            </span>
          )}
        </header>

        {statusLoading ? (
          <div className="min-h-0 flex-1">
            <PageLoader label="Waking the assistant…" />
          </div>
        ) : assistantDisabled ? (
          <UnavailablePanel tierBlocked={tierBlocked} />
        ) : (
          <>
            {historyLoading ? (
              <MessageList messages={[]} tools={[]} stage={null} streaming={false} loading />
            ) : (
              <MessageList
                messages={chat.messages}
                tools={chat.tools}
                stage={chat.stage}
                streaming={chat.streaming}
                onRetry={handleRegenerate}
                onEdit={(messageId, text, files) =>
                  void editMessage(messageId, text, files, "/app/assistant")
                }
              />
            )}

            {isEmpty && (
              <motion.div
                className="shrink-0 space-y-4 border-t border-outline px-4 pb-4 pt-5"
                initial={reduce ? false : { opacity: 0, y: 8 }}
                animate={reduce ? undefined : { opacity: 1, y: 0 }}
                transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              >
                <div>
                  <h2 className="font-display text-lg font-semibold">Transform AI</h2>
                  <p className="mt-0.5 text-sm text-muted">
                    Summarize a file, get format advice, or ask what&apos;s in your library.
                  </p>
                </div>
                <SuggestedPrompts onPick={setInput} seed={suggestSeed} />
              </motion.div>
            )}

            {chat.error && assistantErrorCopy(chat.error.code, chat.error.message).kind !== "tier" && (
              <div
                role="alert"
                className="flex shrink-0 flex-wrap items-center gap-3 border-t border-outline bg-error/5 px-4 py-3"
              >
                <p className="min-w-0 flex-1 text-sm text-on-background">
                  {assistantErrorCopy(chat.error.code, chat.error.message).detail}
                </p>
                {lastUserMessage && (
                  <Button variant="secondary" size="sm" onClick={retryLastTurn}>
                    Try again
                  </Button>
                )}
              </div>
            )}

            {context && (
              <div className="flex shrink-0 items-center gap-2 border-t border-outline px-4 py-2 text-xs text-muted">
                <Paperclip size={13} aria-hidden />
                <span className="truncate">
                  About: {context.fileName ?? "the file you opened"}
                </span>
                <button
                  type="button"
                  onClick={() => setContext(null)}
                  aria-label="Remove file context"
                  className="ml-auto rounded p-1.5 text-muted hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                >
                  <X size={13} />
                </button>
              </div>
            )}

            <Composer
              value={input}
              onChange={setInput}
              onSend={() => send()}
              onStop={stop}
              streaming={chat.streaming}
              attachments={attachments}
              onAttach={attachFile}
              onRemoveAttachment={removeAttachment}
              maxAttachments={attachmentCap}
            />
          </>
        )}
      </section>

      {/* Mobile conversation drawer */}
      <AnimatePresence>
        {drawerOpen && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <motion.div
              className="absolute inset-0 bg-black/60"
              onClick={() => setDrawerOpen(false)}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            />
            <motion.aside
              ref={drawerRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-label="Conversations"
              className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-hidden border-r border-outline bg-surface focus:outline-none"
              initial={reduce ? { opacity: 0 } : { x: "-100%" }}
              animate={reduce ? { opacity: 1 } : { x: 0 }}
              exit={reduce ? { opacity: 0 } : { x: "-100%" }}
              transition={{ type: "spring", stiffness: 320, damping: 32 }}
            >
              <div className="flex shrink-0 items-center justify-between border-b border-outline px-3 py-2">
                <span className="font-display text-sm font-semibold">Conversations</span>
                <button
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  aria-label="Close conversations"
                  className="rounded-md p-2 text-muted hover:bg-surface-variant hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                >
                  <X size={16} />
                </button>
              </div>
              <ConversationList
                conversations={conversations}
                activeId={activeId}
                loading={conversationsLoading}
                onSelect={(id) => void openConversation(id)}
                onNew={newChat}
                onRequestDelete={setDeleteTarget}
              />
            </motion.aside>
          </div>
        )}
      </AnimatePresence>

      {/* Styled delete confirmation — never `window.confirm` */}
      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete conversation"
        description={deleteTarget?.title || "Untitled chat"}
      >
        <div className="space-y-4">
          <p className="text-sm text-muted">
            This deletes the conversation and its messages. It can&apos;t be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

/** Truthful demo-mode marker: the replies are not coming from a language model. */
function EchoBadge() {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-outline bg-surface-variant/60 px-2.5 py-1 text-[11px] font-semibold text-warning"
      title="No language model is configured on this deployment, so replies are generated by a scripted echo backend."
    >
      <Info size={12} weight="fill" aria-hidden />
      Echo/demo mode
    </span>
  );
}

/** Shown when the assistant is off or the account's plan does not include it. */
function UnavailablePanel({ tierBlocked }: { tierBlocked: boolean }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6">
      <div className="max-w-md space-y-3 rounded-xl border border-outline bg-surface-variant/40 p-6 text-center">
        <Sparkle size={22} weight="fill" className="mx-auto text-primary" aria-hidden />
        <h2 className="font-display text-lg font-semibold">
          {tierBlocked ? "Transform AI isn't available on your plan" : "Transform AI is off"}
        </h2>
        <p className="text-sm text-muted">
          {tierBlocked
            ? "Your current plan doesn't include the assistant. Upgrading adds it — standard conversions keep working either way."
            : "The assistant isn't enabled on this deployment right now. Everything else in your workspace still works."}
        </p>
      </div>
    </div>
  );
}
