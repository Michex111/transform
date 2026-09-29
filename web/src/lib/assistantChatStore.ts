// A tiny external store for a Transform AI transcript.
//
// The assistant's state has two homes: the full page owns it with `useState`,
// while the floating mini chat keeps it in a module-level store so the
// conversation survives the panel closing and the route changing. Both go
// through `useAssistantChat`, which reads the state through
// `useSyncExternalStore`; this factory is the backend for the mini chat (and a
// trivial in-memory store tests can drive directly).
//
// Pure and dependency-free: no React import, so the transition rules are
// testable in the Node environment.

import { initialAssistantChatState, type AssistantChatState } from "@/lib/assistantChat";

export type AssistantChatUpdater =
  | AssistantChatState
  | ((previous: AssistantChatState) => AssistantChatState);

export interface AssistantChatStore {
  getState(): AssistantChatState;
  /** Apply a state or an updater. Notifies subscribers only on a real change. */
  setState(update: AssistantChatUpdater): void;
  /** Register a listener; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void;
}

export function createAssistantChatStore(initial?: AssistantChatState): AssistantChatStore {
  let state = initial ?? initialAssistantChatState();
  const listeners = new Set<() => void>();

  return {
    getState: () => state,
    setState(update) {
      const next = typeof update === "function" ? update(state) : update;
      // `applyStreamEvent` returns the identical object when an event changes
      // nothing (an orphan delta, a duplicate tool). Skipping the notification
      // then avoids a pointless re-render on every ignored frame.
      if (next === state) return;
      state = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
