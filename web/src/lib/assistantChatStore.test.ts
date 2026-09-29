// Tests for the external chat store: value and updater forms, change-only
// notification, and unsubscribe.

import { describe, expect, it, vi } from "vitest";
import { initialAssistantChatState, startAssistantTurn } from "@/lib/assistantChat";
import { createAssistantChatStore } from "@/lib/assistantChatStore";

describe("createAssistantChatStore", () => {
  it("starts from the initial state and applies a value", () => {
    const store = createAssistantChatStore();
    expect(store.getState()).toEqual(initialAssistantChatState());
    const next = startAssistantTurn(initialAssistantChatState(), "hi");
    store.setState(next);
    expect(store.getState()).toBe(next);
  });

  it("applies an updater against the current state", () => {
    const store = createAssistantChatStore();
    store.setState((prev) => startAssistantTurn(prev, "hi"));
    expect(store.getState().messages[0].content).toBe("hi");
  });

  it("notifies subscribers on change and stops after unsubscribe", () => {
    const store = createAssistantChatStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);

    store.setState(startAssistantTurn(initialAssistantChatState(), "a"));
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    store.setState(startAssistantTurn(store.getState(), "b"));
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("does not notify when the updater returns the same object", () => {
    const store = createAssistantChatStore();
    const listener = vi.fn();
    store.subscribe(listener);
    store.setState((prev) => prev);
    expect(listener).not.toHaveBeenCalled();
  });
});
