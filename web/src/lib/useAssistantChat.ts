// The streaming chat engine, shared by the assistant page and the floating
// mini chat.
//
// Everything about talking to `POST /assistant/chat` lives here once: the SSE
// subscription, the abort handle, the transcript transitions, and how an error
// is routed. The two surfaces differ only in where the state is stored (page
// state vs. a module-level store) and in what they do when a turn finishes, so
// those are the parameters — not two copies of the wiring.

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { AssistantAttachment, AssistantMessage } from "@/api/types";
import { ASSISTANT_ATTACHMENT_LIMIT_EXCEEDED, ASSISTANT_ATTACHMENT_NOT_FOUND } from "@/api/types";
import type { ApiClient } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import {
  applyStreamEvent,
  editTurn,
  initialAssistantChatState,
  mergeServerMessages,
  regenerateTurn,
  serverMessageId,
  startAssistantTurn,
  stopAssistantTurn,
  undoLastTurn,
  type AssistantChatState,
} from "@/lib/assistantChat";
import { attachmentFileIds } from "@/lib/assistantAttachments";
import type { AssistantChatStore } from "@/lib/assistantChatStore";

/** What a turn carries besides its text. */
export interface AssistantTurnInput {
  /** Files to attach (their ids ride as `file_ids`). */
  attachments?: AssistantAttachment[];
  /** The page the question came from, e.g. "/app/dashboard". */
  context?: string;
}

export interface UseAssistantChatOptions {
  /** Where the transcript lives. Stable for the hook's lifetime. */
  store: AssistantChatStore;
  /** A turn finished; `conversationId` may now be newly minted. */
  onTurnDone?: (conversationId: string) => void;
  /** A turn failed. The transcript has already recorded the error. */
  onError?: (code: string, message: string) => void;
  /**
   * The API rejected the turn's attachments (404 `ATTACHMENT_NOT_FOUND`). The
   * failed turn has already been removed; the caller restores the typed message
   * (with the gone files stripped) so the user can resend.
   */
  onAttachmentNotFound?: (prompt: string, attachments: AssistantAttachment[]) => void;
  /**
   * The API refused the turn because it carried more files than the plan allows
   * (403 `ATTACHMENT_LIMIT_EXCEEDED`). The failed turn has already been removed;
   * the caller restores the message AND its whole attachment list (nothing is
   * stripped — the files are fine, there are just too many) and surfaces the
   * server's message, which names the plan's number.
   */
  onAttachmentLimitExceeded?: (
    prompt: string,
    attachments: AssistantAttachment[],
    message: string,
  ) => void;
}

export interface UseAssistantChatResult {
  chat: AssistantChatState;
  /** True while a stored conversation's messages are being fetched. */
  historyLoading: boolean;
  /** Send a turn. No-op while a turn is already in flight. */
  send: (text: string, input?: AssistantTurnInput) => void;
  /** Abort the in-flight turn, keeping whatever text arrived. */
  stop: () => void;
  /** Re-run the turn behind `messageId`. Returns false when it cannot. */
  regenerate: (messageId: string, context?: string) => boolean;
  /**
   * Replace the user message `messageId` with `text` (+ `attachments`) and
   * resend it, dropping every later turn.
   *
   * Returns false when the id is not an editable user message (an unknown id,
   * an assistant id, or a turn that cannot be replaced). The delete of the
   * persisted transcript and the resend happen asynchronously after that.
   */
  editMessage: (
    messageId: string,
    text: string,
    attachments?: AssistantAttachment[],
    context?: string,
  ) => boolean;
  /** Re-run the most recent turn after a failure (the error banner's action). */
  retryLastTurn: () => boolean;
  /** Clear the transcript and begin a new conversation. */
  newChat: () => void;
  /**
   * Replace the transcript with a persisted conversation. `loader` fetches the
   * server messages; a superseded load is ignored. Rejects if the loader does,
   * so the caller can report it.
   */
  loadConversation: (
    conversationId: string,
    loader: () => Promise<AssistantMessage[]>,
  ) => Promise<void>;
}

/**
 * Truncate a persisted conversation so it ends before `messageId`.
 *
 * The server transcript is the source of truth, so an edit has to delete the
 * original turn (and the answer it produced) or reopening the conversation
 * would show the message the user believed they had replaced. The caller
 * re-sends the edited turn immediately afterwards, which appends at the freed
 * position. Backed by `DELETE
 * /api/v1/assistant/conversations/{conversation_id}/messages/{message_id}`.
 *
 * Returns `null` when there is nothing to truncate: no persisted conversation,
 * or a message whose server id we do not know yet (a turn sent this session
 * before its `done` frame arrived). Callers treat that as "edit locally only",
 * which is why this is a nullable result rather than a no-op promise.
 */
function deletePersistedMessage(
  client: Pick<ApiClient, "assistantDeleteMessage">,
  conversationId: string,
  messageId: string,
): Promise<void> {
  return client.assistantDeleteMessage(conversationId, messageId);
}

export function useAssistantChat({
  store,
  onTurnDone,
  onError,
  onAttachmentNotFound,
  onAttachmentLimitExceeded,
}: UseAssistantChatOptions): UseAssistantChatResult {
  const { api: client } = useAuth();
  const chat = useSyncExternalStore(store.subscribe, store.getState, store.getState);

  const [historyLoading, setHistoryLoading] = useState(false);
  // Abort function of the in-flight stream, if any.
  const abortRef = useRef<(() => void) | null>(null);
  // Guards a conversation load against a newer one landing first.
  const loadRef = useRef(0);
  // The last turn we sent, so a regenerate/retry can re-ask it (with its files).
  const lastSentRef = useRef<{ text: string; input: AssistantTurnInput } | null>(null);

  // Abort any live stream when the owner unmounts.
  useEffect(
    () => () => {
      abortRef.current?.();
      abortRef.current = null;
    },
    [],
  );

  function handleError(code: string, message: string) {
    if (code === ASSISTANT_ATTACHMENT_NOT_FOUND) {
      // The turn cannot succeed as sent. Drop it entirely (no blank answer, no
      // error banner) and let the caller restore the message so the user can
      // resend without the missing file.
      const sent = lastSentRef.current;
      abortRef.current = null;
      store.setState((prev) => undoLastTurn(prev));
      if (sent) onAttachmentNotFound?.(sent.text, sent.input.attachments ?? []);
      return;
    }
    if (code === ASSISTANT_ATTACHMENT_LIMIT_EXCEEDED) {
      // The plan allows fewer files than the turn carried (a plan change, or a
      // client cap ahead of the server's). Unlike ATTACHMENT_NOT_FOUND nothing
      // is wrong with the files, so nothing is stripped: drop the failed turn
      // and hand back the message with its whole attachment list, so the user
      // can remove one file and resend. The server's own message is passed on
      // because it already contains the plan's number.
      const sent = lastSentRef.current;
      if (sent) {
        abortRef.current = null;
        store.setState((prev) => undoLastTurn(prev));
        onAttachmentLimitExceeded?.(sent.text, sent.input.attachments ?? [], message);
        return;
      }
      // No sent turn to restore (unexpected for a send): fall through to the
      // ordinary error surface rather than swallowing the reason.
    }
    store.setState((prev) => applyStreamEvent(prev, { type: "error", code, message }));
    onError?.(code, message);
  }

  /** Open the stream for `text`. Assumes the turn is already in state. */
  function streamTurn(text: string, input: AssistantTurnInput | undefined, conversationId: string | null) {
    const fileIds = attachmentFileIds(input?.attachments ?? []);
    abortRef.current = client.assistantChat(
      {
        message: text,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(fileIds.length ? { file_ids: fileIds } : {}),
        ...(input?.context ? { context: input.context } : {}),
      },
      {
        onEvent: (event) => {
          store.setState((prev) => applyStreamEvent(prev, event));
          if (event.type === "done") {
            if (event.conversation_id) onTurnDone?.(event.conversation_id);
          } else if (event.type === "error") {
            handleError(event.code, event.message);
          }
        },
        onError: (err) => handleError(err.code, err.message),
      },
    );
  }

  function send(raw: string, input: AssistantTurnInput = {}) {
    const text = raw.trim();
    if (!text || store.getState().streaming) return;
    const attachments = input.attachments ?? [];
    lastSentRef.current = { text, input: { ...input, attachments } };
    store.setState((prev) => startAssistantTurn(prev, text, attachments));
    streamTurn(text, input, store.getState().conversationId);
  }

  function stop() {
    abortRef.current?.();
    abortRef.current = null;
    store.setState((prev) => stopAssistantTurn(prev));
  }

  function regenerate(messageId: string, context?: string): boolean {
    const current = store.getState();
    // Abort any live stream first: a Retry pressed while another turn is
    // streaming must end that turn rather than race two responses into the
    // transcript. `stopAssistantTurn` keeps the partial text and clears the
    // pending flag, which is the state `regenerateTurn` requires.
    abortRef.current?.();
    abortRef.current = null;
    const base = current.streaming ? stopAssistantTurn(current) : current;
    if (base !== current) store.setState(base);

    const result = regenerateTurn(base, messageId);
    if (!result) return false;

    const input: AssistantTurnInput = { attachments: result.attachments, context };
    lastSentRef.current = { text: result.prompt, input };
    store.setState(startAssistantTurn(result.state, result.prompt, result.attachments));
    streamTurn(result.prompt, input, result.state.conversationId);
    return true;
  }

  /**
   * Edit a user message and resend it.
   *
   * The order below is deliberate:
   *
   *  1. Abort any live stream — the edit replaces a turn, and `editTurn`
   *     refuses to run mid-stream.
   *  2. Apply the edit locally and open the new turn's placeholders, so the
   *     transcript updates the instant the user hits Save instead of after a
   *     network round trip.
   *  3. Truncate the persisted transcript, if this message has a server id.
   *     That delete must land *before* the new turn is sent, or the server
   *     would keep both the old and the new turn.
   *  4. Send.
   *
   * A message that only ever existed in this tab (a local id, or a turn whose
   * `done` frame carried no `user_message_id`) has nothing on the server to
   * truncate, so step 3 is skipped and the resend happens immediately. That is
   * not a divergence: the server never had that turn to begin with.
   *
   * If the delete fails — a 404 because the message is gone or not owned, or any
   * other error — the user's intent was to send, so we still send; the failure
   * is reported through the hook's existing `onError` path (the same one a
   * failed turn uses, which the page turns into a toast) rather than swallowed.
   * The one consequence is that the server keeps the old turn, so the transcript
   * on screen is ahead of the persisted one until the next reload.
   */
  function editMessage(
    messageId: string,
    text: string,
    attachments: AssistantAttachment[] = [],
    context?: string,
  ): boolean {
    const current = store.getState();
    abortRef.current?.();
    abortRef.current = null;
    const base = current.streaming ? stopAssistantTurn(current) : current;
    if (base !== current) store.setState(base);

    const target = base.messages.find((message) => message.id === messageId);
    const result = editTurn(base, messageId);
    const prompt = text.trim();
    if (!target || !result || !prompt) return false;

    // The id of the row to remove on the server, when there is one. A message
    // sent in this session learns it from the `done` frame; a message loaded
    // from the server is its own id (`serverMessageId` resolves both).
    const persistedId = serverMessageId(target);
    const conversationId = result.state.conversationId;

    const input: AssistantTurnInput = { attachments, context };
    lastSentRef.current = { text: prompt, input };
    store.setState(startAssistantTurn(result.state, prompt, attachments));

    const sendEdited = () => {
      // The user may have pressed Stop (or started a new chat) while the delete
      // was in flight. A superseded edit must not stream into the transcript.
      if (!store.getState().streaming) return;
      streamTurn(prompt, input, conversationId);
    };

    const truncated =
      conversationId && persistedId
        ? deletePersistedMessage(client, conversationId, persistedId)
        : null;

    if (truncated) {
      void truncated.then(sendEdited, (err: unknown) => {
        onError?.(
          "EDIT_TRUNCATE_FAILED",
          err instanceof Error ? err.message : "Could not update the conversation.",
        );
        sendEdited();
      });
    } else {
      sendEdited();
    }

    return true;
  }

  function retryLastTurn(): boolean {
    const current = store.getState();
    if (current.streaming) return false;

    // Prefer the turn we just sent; fall back to the transcript, which is what
    // a reopened conversation has after a failure.
    let text = lastSentRef.current?.text ?? "";
    let attachments = lastSentRef.current?.input.attachments ?? [];
    if (!text) {
      for (let i = current.messages.length - 1; i >= 0; i--) {
        if (current.messages[i].role === "user") {
          text = current.messages[i].content;
          attachments = current.messages[i].attachments;
          break;
        }
      }
    }
    if (!text) return false;

    const context = lastSentRef.current?.input.context;
    const undone = undoLastTurn(current);
    const input: AssistantTurnInput = { attachments, context };
    lastSentRef.current = { text, input };
    store.setState(startAssistantTurn(undone, text, attachments));
    streamTurn(text, input, undone.conversationId);
    return true;
  }

  function newChat() {
    abortRef.current?.();
    abortRef.current = null;
    loadRef.current += 1;
    lastSentRef.current = null;
    setHistoryLoading(false);
    store.setState(initialAssistantChatState());
  }

  async function loadConversation(
    conversationId: string,
    loader: () => Promise<AssistantMessage[]>,
  ): Promise<void> {
    abortRef.current?.();
    abortRef.current = null;
    const requestId = ++loadRef.current;
    setHistoryLoading(true);
    store.setState({ ...initialAssistantChatState(), conversationId });
    try {
      const messages = await loader();
      if (requestId !== loadRef.current) return;
      store.setState((prev) => mergeServerMessages(prev, messages));
    } finally {
      if (requestId === loadRef.current) setHistoryLoading(false);
    }
  }

  return {
    chat,
    historyLoading,
    send,
    stop,
    regenerate,
    editMessage,
    retryLastTurn,
    newChat,
    loadConversation,
  };
}
