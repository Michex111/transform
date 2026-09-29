// Tests for the assistant transcript reducer.
//
// This is the only place the rules for a streamed answer are written down: what
// a delta does, when `done` overrides streamed text, how tool activity moves
// from running to done, and what survives an error. Each case below is a real
// ordering the SSE stream can produce.

import { describe, expect, it } from "vitest";
import type { AssistantArtifact, AssistantMessage, AssistantStreamEvent } from "@/api/types";
import type { UiJob } from "@/jobs/jobStore";
import {
  applyJobProgress,
  applyStreamEvent,
  assistantErrorCopy,
  conversationTitle,
  editTurn,
  initialAssistantChatState,
  mergeArtifacts,
  mergeServerMessages,
  regenerateTurn,
  serverMessageId,
  stageLabel,
  startAssistantTurn,
  stopAssistantTurn,
  undoLastTurn,
  type AssistantChatState,
} from "@/lib/assistantChat";

function artifact(overrides: Partial<AssistantArtifact> = {}): AssistantArtifact {
  return { type: "file", id: "f1", name: "report.pdf", ...overrides };
}

function serverMessage(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return { id: "m1", role: "assistant", content: "hello", ...overrides };
}

/** A state with one user turn already started. */
function started(text = "Summarize my PDF"): AssistantChatState {
  return startAssistantTurn(initialAssistantChatState(), text);
}

describe("startAssistantTurn", () => {
  it("appends the user message and a pending assistant placeholder", () => {
    const state = started("Summarize my PDF");
    expect(state.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(state.messages[0].content).toBe("Summarize my PDF");
    expect(state.messages[1].pending).toBe(true);
    expect(state.streaming).toBe(true);
    expect(state.error).toBeNull();
  });

  it("trims the user's message", () => {
    expect(started("  hello  ").messages[0].content).toBe("hello");
  });

  it("is a no-op for blank input", () => {
    const state = initialAssistantChatState();
    expect(startAssistantTurn(state, "   ")).toBe(state);
  });

  it("clears the previous turn's tools, stage and error", () => {
    let state = started("first");
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "running" } });
    state = applyStreamEvent(state, { type: "status", stage: "writing" });
    state = applyStreamEvent(state, { type: "error", code: "INTERNAL_ERROR", message: "boom" });

    const next = startAssistantTurn(state, "second");
    expect(next.tools).toEqual([]);
    expect(next.stage).toBeNull();
    expect(next.error).toBeNull();
  });

  it("carries the turn's attachments on the user message only", () => {
    const state = startAssistantTurn(initialAssistantChatState(), "convert this", [
      { id: "f1", name: "resume.pdf", extension: "pdf" },
    ]);
    expect(state.messages[0].attachments).toEqual([
      { id: "f1", name: "resume.pdf", extension: "pdf" },
    ]);
    expect(state.messages[1].attachments).toEqual([]);
  });

  it("copies the attachment list so later composer edits cannot mutate it", () => {
    const attachments = [{ id: "f1", name: "resume.pdf" }];
    const state = startAssistantTurn(initialAssistantChatState(), "x", attachments);
    attachments[0].name = "changed.pdf";
    expect(state.messages[0].attachments[0].name).toBe("resume.pdf");
  });
});

describe("applyStreamEvent — deltas", () => {
  it("appends deltas in arrival order", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "delta", text: "Hello" });
    state = applyStreamEvent(state, { type: "delta", text: ", world" });
    expect(state.messages[1].content).toBe("Hello, world");
    expect(state.streaming).toBe(true);
  });

  it("ignores a delta with no pending assistant message", () => {
    const state = initialAssistantChatState();
    expect(applyStreamEvent(state, { type: "delta", text: "orphan" })).toBe(state);
  });

  it("ignores a delta after the turn was finalised", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "delta", text: "done text" });
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "srv-1",
      content: "done text",
      artifacts: [],
    });
    const after = applyStreamEvent(state, { type: "delta", text: " late" });
    expect(after).toBe(state);
    expect(after.messages[1].content).toBe("done text");
  });

  it("does not mutate the state it is given", () => {
    const state = started();
    const before = JSON.stringify(state);
    applyStreamEvent(state, { type: "delta", text: "x" });
    expect(JSON.stringify(state)).toBe(before);
  });
});

describe("applyStreamEvent — status and tools", () => {
  it("records the stage", () => {
    const state = applyStreamEvent(started(), { type: "status", stage: "searching" });
    expect(state.stage).toBe("searching");
  });

  it("adds a running tool and moves it to done with its summary", () => {
    let state = started();
    state = applyStreamEvent(state, {
      type: "tool",
      tool: { name: "search_files", label: "Searching your files", status: "running" },
    });
    expect(state.tools).toEqual([
      { name: "search_files", label: "Searching your files", status: "running" },
    ]);

    state = applyStreamEvent(state, {
      type: "tool",
      tool: { name: "search_files", status: "done", summary: "Found 3 matches" },
    });
    expect(state.tools).toEqual([
      {
        name: "search_files",
        label: "Searching your files",
        status: "done",
        summary: "Found 3 matches",
      },
    ]);
  });

  it("keeps the tool's label when the done frame omits it", () => {
    let state = started();
    state = applyStreamEvent(state, {
      type: "tool",
      tool: { name: "search", label: "Searching", status: "running" },
    });
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "done" } });
    expect(state.tools[0].label).toBe("Searching");
  });

  it("does not duplicate a tool that is reported twice", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "running" } });
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "running" } });
    expect(state.tools).toHaveLength(1);
  });

  it("collects artifacts reported by a finished tool", () => {
    let state = started();
    state = applyStreamEvent(state, {
      type: "tool",
      tool: { name: "search", status: "done", artifacts: [artifact()] },
    });
    expect(state.messages[1].artifacts).toEqual([artifact()]);
  });
});

describe("applyStreamEvent — artifacts", () => {
  it("records an artifact on the pending message", () => {
    const state = applyStreamEvent(started(), { type: "artifact", artifact: artifact() });
    expect(state.messages[1].artifacts).toEqual([artifact()]);
  });

  it("deduplicates the same artifact arriving twice", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "artifact", artifact: artifact() });
    state = applyStreamEvent(state, { type: "artifact", artifact: artifact() });
    expect(state.messages[1].artifacts).toHaveLength(1);
  });

  it("lets a later, richer report of the same artifact win", () => {
    let state = started();
    state = applyStreamEvent(state, {
      type: "artifact",
      artifact: artifact({ name: "report" }),
    });
    state = applyStreamEvent(state, {
      type: "artifact",
      artifact: artifact({ name: "report-final.pdf" }),
    });
    expect(state.messages[1].artifacts).toEqual([artifact({ name: "report-final.pdf" })]);
  });

  it("keeps distinct artifacts apart even with the same id", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "artifact", artifact: artifact() });
    state = applyStreamEvent(state, {
      type: "artifact",
      artifact: artifact({ type: "job", id: "f1" }),
    });
    expect(state.messages[1].artifacts).toHaveLength(2);
  });

  it("ignores an artifact with no pending message", () => {
    const state = initialAssistantChatState();
    expect(applyStreamEvent(state, { type: "artifact", artifact: artifact() })).toBe(state);
  });
});

describe("applyStreamEvent — done", () => {
  const done: AssistantStreamEvent = {
    type: "done",
    conversation_id: "conv-9",
    message_id: "srv-42",
    content: "The final answer.",
    artifacts: [],
  };

  it("finalises the pending message with the server's copy", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "delta", text: "The fina" });
    state = applyStreamEvent(state, done);

    const message = state.messages[1];
    expect(message.id).toBe("srv-42");
    expect(message.content).toBe("The final answer.");
    expect(message.pending).toBe(false);
    expect(state.streaming).toBe(false);
    expect(state.stage).toBeNull();
    expect(state.conversationId).toBe("conv-9");
  });

  it("keeps accumulated artifacts when the done frame carries none", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "artifact", artifact: artifact() });
    state = applyStreamEvent(state, done);
    expect(state.messages[1].artifacts).toEqual([artifact()]);
  });

  it("replaces accumulated artifacts when the done frame carries its own list", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "artifact", artifact: artifact({ id: "old" }) });
    state = applyStreamEvent(state, { ...done, artifacts: [artifact({ id: "final" })] });
    expect(state.messages[1].artifacts.map((a) => a.id)).toEqual(["final"]);
  });

  it("creates a message for a tool-only answer that never streamed text", () => {
    const state = applyStreamEvent(started(), done);
    expect(state.messages).toHaveLength(2);
    expect(state.messages[1].content).toBe("The final answer.");
  });

  it("marks any still-running tool as done", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "running" } });
    state = applyStreamEvent(state, done);
    expect(state.tools[0].status).toBe("done");
  });

  it("keeps a conversation id that the frame omits", () => {
    const withId: AssistantChatState = { ...started(), conversationId: "existing" };
    const state = applyStreamEvent(withId, { ...done, conversation_id: "" });
    expect(state.conversationId).toBe("existing");
  });
});

describe("applyStreamEvent — steps survive completion", () => {
  // The "thought process" must read the same whether a turn just happened or was
  // reloaded. It used to be visible only while streaming (from `state.tools`)
  // and then disappear the moment `done` arrived.
  const toolDone: AssistantStreamEvent = {
    type: "tool",
    tool: { name: "list_files", label: "Looking through your files", status: "done", summary: "Found 3 files" },
  };
  // Local to this block: the `done` event above is scoped to its own describe.
  const finished: AssistantStreamEvent = {
    type: "done",
    conversation_id: "c1",
    message_id: "m1",
    content: "The final answer.",
    artifacts: [],
  };

  it("publishes the finished tool activity onto the completed answer", () => {
    let state = started();
    state = applyStreamEvent(state, toolDone);
    state = applyStreamEvent(state, finished);

    expect(state.messages[1].steps).toEqual([
      { name: "list_files", label: "Looking through your files", summary: "Found 3 files" },
    ]);
  });

  it("publishes steps onto a tool-only answer that never streamed text", () => {
    let state = started();
    state = applyStreamEvent(state, toolDone);
    // No delta at all, so the finaliser takes the `at === -1` branch.
    const state2 = applyStreamEvent(state, { ...finished, content: "Done." });
    expect(state2.messages[1].steps?.[0]?.name).toBe("list_files");
  });

  it("leaves a plain answer with no steps at all", () => {
    const state = applyStreamEvent(started(), finished);
    expect(state.messages[1].steps).toBeUndefined();
  });

  it("omits a tool the server never reported finishing", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "tool", tool: { name: "search", status: "running" } });
    state = applyStreamEvent(state, finished);
    expect(state.messages[1].steps).toBeUndefined();
  });

  it("keeps steps when the user stops the turn", () => {
    let state = started();
    state = applyStreamEvent(state, toolDone);
    state = stopAssistantTurn(state);
    expect(state.messages[1].steps?.[0]?.name).toBe("list_files");
    expect(state.messages[1].pending).toBe(false);
  });

  it("keeps steps when the turn fails", () => {
    let state = started();
    state = applyStreamEvent(state, toolDone);
    state = applyStreamEvent(state, { type: "error", code: "INTERNAL_ERROR", message: "boom" });
    expect(state.messages[1].steps?.[0]?.name).toBe("list_files");
  });
});

describe("applyStreamEvent — error", () => {
  it("keeps partial text, stops streaming and records the code", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "delta", text: "Partial ans" });
    state = applyStreamEvent(state, {
      type: "error",
      code: "QUOTA_EXCEEDED",
      message: "Hourly limit reached",
    });

    expect(state.messages[1].content).toBe("Partial ans");
    expect(state.messages[1].pending).toBe(false);
    expect(state.streaming).toBe(false);
    expect(state.error).toEqual({ code: "QUOTA_EXCEEDED", message: "Hourly limit reached" });
  });

  it("still records the error when there is no pending message", () => {
    const state = applyStreamEvent(initialAssistantChatState(), {
      type: "error",
      code: "INTERNAL_ERROR",
      message: "nope",
    });
    expect(state.error?.code).toBe("INTERNAL_ERROR");
  });
});

describe("stopAssistantTurn", () => {
  it("ends the turn without an error, keeping the partial answer", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "delta", text: "half" });
    state = applyStreamEvent(state, { type: "tool", tool: { name: "t", status: "running" } });

    const stopped = stopAssistantTurn(state);
    expect(stopped.streaming).toBe(false);
    expect(stopped.stage).toBeNull();
    expect(stopped.error).toBeNull();
    expect(stopped.messages[1].content).toBe("half");
    expect(stopped.messages[1].pending).toBe(false);
    expect(stopped.tools[0].status).toBe("done");
  });
});

describe("undoLastTurn", () => {
  it("drops the local user message and its failed answer", () => {
    let state = started("try me");
    state = applyStreamEvent(state, { type: "error", code: "INTERNAL_ERROR", message: "boom" });
    const undone = undoLastTurn(state);
    expect(undone.messages).toEqual([]);
    expect(undone.error).toBeNull();
    expect(undone.streaming).toBe(false);
  });

  it("leaves server history alone", () => {
    const state: AssistantChatState = {
      ...initialAssistantChatState(),
      messages: mergeServerMessages(initialAssistantChatState(), [
        serverMessage({ id: "m1", role: "user", content: "hi" }),
        serverMessage({ id: "m2", role: "assistant", content: "hello" }),
      ]).messages,
    };
    expect(undoLastTurn(state).messages).toHaveLength(2);
  });
});

describe("regenerateTurn", () => {
  it("truncates back to the user message and hands back its prompt", () => {
    let state = started("Summarize it");
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "srv-1",
      content: "Here's the summary.",
      artifacts: [],
    });
    const result = regenerateTurn(state, "srv-1");
    expect(result).not.toBeNull();
    expect(result?.prompt).toBe("Summarize it");
    expect(result?.state.messages).toEqual([]);
    expect(result?.state.streaming).toBe(false);
  });

  it("keeps the attachments the original turn was sent with", () => {
    const attachments = [{ id: "f1", name: "resume.pdf", extension: "pdf" }];
    let state = startAssistantTurn(initialAssistantChatState(), "convert this", attachments);
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "srv-2",
      content: "Done.",
      artifacts: [],
    });
    const result = regenerateTurn(state, "srv-2");
    expect(result?.attachments).toEqual(attachments);
    // A copy, not the same array, so re-sending cannot alias the old message.
    expect(result?.attachments).not.toBe(attachments);
  });

  it("drops everything after the regenerated turn", () => {
    let state = started("first");
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "a1",
      content: "one",
      artifacts: [],
    });
    state = startAssistantTurn(state, "second");
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "a2",
      content: "two",
      artifacts: [],
    });
    const result = regenerateTurn(state, "a1");
    expect(result?.state.messages).toEqual([]);
    expect(result?.prompt).toBe("first");
  });

  it("returns null for an unknown id, a user id, or while streaming", () => {
    const state = started("hi");
    const userId = state.messages[0].id;
    expect(regenerateTurn(state, "nope")).toBeNull();
    expect(regenerateTurn(state, userId)).toBeNull();
    // Still streaming: the caller must stop first.
    expect(regenerateTurn({ ...state, streaming: true }, state.messages[1].id)).toBeNull();
  });

  it("returns null when no user message precedes the answer", () => {
    const state: AssistantChatState = {
      ...initialAssistantChatState(),
      messages: mergeServerMessages(initialAssistantChatState(), [
        serverMessage({ id: "a1", content: "hello" }),
      ]).messages,
    };
    expect(regenerateTurn(state, "a1")).toBeNull();
  });
});

describe("mergeServerMessages", () => {
  it("replaces the transcript with the server's order", () => {
    const state = started("local question");
    const merged = mergeServerMessages(state, [
      serverMessage({ id: "m1", role: "user", content: "server question" }),
      serverMessage({ id: "m2", content: "server answer" }),
    ]);
    expect(merged.messages.map((m) => m.content)).toEqual([
      "server question",
      "server answer",
      "local question",
      "",
    ]);
  });

  it("does not duplicate a message the server already returned", () => {
    const state = started("local");
    // Walk the turn to its finalised, server-identified form.
    const finalised = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "srv-1",
      content: "answer",
      artifacts: [],
    });
    const merged = mergeServerMessages(finalised, [
      serverMessage({ id: "srv-1", content: "answer" }),
    ]);
    expect(merged.messages.filter((m) => m.id === "srv-1")).toHaveLength(1);
    expect(merged.messages[0].local).toBe(false);
  });

  it("clears a stale error when a conversation is loaded", () => {
    let state = started();
    state = applyStreamEvent(state, { type: "error", code: "X", message: "y" });
    expect(mergeServerMessages(state, []).error).toBeNull();
  });

  it("collapses a persisted turn into one assistant bubble without the tool JSON", () => {
    // The reopened-conversation bug: the server stores a tool request, a raw
    // tool-result row, then the answer. `mergeServerMessages` must hand the UI
    // one assistant bubble with its steps, never the raw JSON.
    const messages: AssistantMessage[] = [
      { id: "u1", role: "user", content: "What's in my report?" },
      {
        id: "req",
        role: "assistant",
        content: "",
        meta: { tool_calls: [{ id: "c1", name: "list_files" }] },
      },
      {
        id: "t1",
        role: "tool",
        content: '{"matches":[{"id":"f1"}],"count":2}',
        tool_name: "list_files",
        label: "Looking through your files",
        summary: "Found 2 files",
      },
      {
        id: "a1",
        role: "assistant",
        content: "Two files match.",
        artifacts: [{ type: "file", id: "f1", name: "a.pdf" }],
      },
    ];

    const merged = mergeServerMessages(initialAssistantChatState(), messages);
    expect(merged.messages).toHaveLength(2);
    expect(merged.messages[1].role).toBe("assistant");
    expect(merged.messages[1].content).toBe("Two files match.");
    expect(merged.messages[1].steps).toEqual([
      { name: "list_files", label: "Looking through your files", summary: "Found 2 files" },
    ]);
    expect(merged.messages[1].artifacts).toEqual([{ type: "file", id: "f1", name: "a.pdf" }]);
    expect(merged.messages.map((message) => message.content).join("\n")).not.toContain("matches");
  });

  it("keeps a reopened user turn's attachments", () => {
    // `normalizeAssistantMessage` lifts `meta.attachments` onto the typed
    // `attachments` field; the transcript builder reads that field and puts it
    // on the user bubble so the chips survive a reload.
    const merged = mergeServerMessages(initialAssistantChatState(), [
      serverMessage({
        id: "u1",
        role: "user",
        content: "convert this",
        attachments: [{ id: "f1", name: "resume.pdf", extension: "pdf" }],
      }),
      serverMessage({ id: "a1", content: "Sure." }),
    ]);
    expect(merged.messages[0].attachments).toEqual([
      { id: "f1", name: "resume.pdf", extension: "pdf" },
    ]);
    expect(merged.messages[1].attachments).toEqual([]);
  });

  it("tolerates an empty body", () => {
    expect(mergeServerMessages(initialAssistantChatState(), []).messages).toEqual([]);
  });
});

describe("mergeArtifacts", () => {
  it("appends new artifacts and keeps first-seen order", () => {
    const first = [artifact({ id: "a" })];
    const merged = mergeArtifacts(first, [artifact({ id: "b" }), artifact({ id: "c" })]);
    expect(merged.map((a) => a.id)).toEqual(["a", "b", "c"]);
  });

  it("replaces in place rather than appending a duplicate", () => {
    const merged = mergeArtifacts([artifact({ id: "a", name: "old" })], [
      artifact({ id: "a", name: "new" }),
    ]);
    expect(merged).toEqual([artifact({ id: "a", name: "new" })]);
  });
});

describe("conversationTitle", () => {
  it("uses the message when it is short", () => {
    expect(conversationTitle("Summarize my PDF")).toBe("Summarize my PDF");
  });

  it("collapses whitespace and trims", () => {
    expect(conversationTitle("  Summarize\n  my   PDF ")).toBe("Summarize my PDF");
  });

  it("falls back to a placeholder for empty input", () => {
    expect(conversationTitle("   ")).toBe("New chat");
  });

  it("clips long text on a word boundary", () => {
    const title = conversationTitle(
      "Please explain in great detail what the best format for a resume is and why",
    );
    expect(title.endsWith("…")).toBe(true);
    expect(title.length).toBeLessThanOrEqual(49);
    // No dangling half word before the ellipsis.
    expect(title).not.toMatch(/\s…$/);
  });
});

describe("stageLabel", () => {
  it("maps the known stages", () => {
    expect(stageLabel("thinking")).toBe("Thinking…");
    expect(stageLabel("searching")).toBe("Looking through your files…");
  });

  it("falls back for an unknown or absent stage", () => {
    expect(stageLabel(null)).toBe("Working…");
    expect(stageLabel("teleporting")).toBe("Working…");
  });
});

describe("assistantErrorCopy", () => {
  it("explains the hourly quota", () => {
    const copy = assistantErrorCopy("QUOTA_EXCEEDED", "ignored");
    expect(copy.kind).toBe("quota");
    expect(copy.toast).toBe("Hourly AI limit reached — try again later");
  });

  it("routes an unavailable tier to the panel copy", () => {
    expect(assistantErrorCopy("AI_NOT_AVAILABLE_FOR_TIER", "ignored").kind).toBe("tier");
  });

  it("surfaces the server's own message for anything else", () => {
    const copy = assistantErrorCopy("SOMETHING_NEW", "The model timed out");
    expect(copy.kind).toBe("generic");
    expect(copy.detail).toBe("The model timed out");
  });

  it("still says something when the server sent no message", () => {
    expect(assistantErrorCopy("INTERNAL_ERROR", "").toast.length).toBeGreaterThan(0);
  });
});

describe("applyStreamEvent — the turn's user message gets its server id", () => {
  // Why this exists: a message sent in *this* session has no server id until
  // the turn finishes, and the edit flow needs that id to truncate the
  // persisted transcript without a reload.
  const finished: AssistantStreamEvent = {
    type: "done",
    conversation_id: "c1",
    message_id: "a1",
    content: "Done.",
    artifacts: [],
    user_message_id: "u1",
  };

  it("stamps the user message that produced the answer", () => {
    let state = started("hi");
    const localId = state.messages[0].id;
    state = applyStreamEvent(state, finished);

    expect(state.messages[0].serverId).toBe("u1");
    // The local id is kept as the React key; the server id rides alongside it.
    expect(state.messages[0].id).toBe(localId);
    expect(state.messages[0].local).toBe(true);
    expect(serverMessageId(state.messages[0])).toBe("u1");
  });

  it("stamps the user message even when the answer is tool-only", () => {
    // The `at === -1` branch: no delta ever arrived, so the answer is pushed
    // rather than updated. The user message must still be found by walking back.
    let state = started("hi");
    state = applyStreamEvent(state, {
      type: "tool",
      tool: { name: "search", status: "done", summary: "found" },
    });
    state = applyStreamEvent(state, finished);
    expect(state.messages[0].serverId).toBe("u1");
  });

  it("leaves serverId unset when the frame omits it", () => {
    let state = started("hi");
    state = applyStreamEvent(state, { ...finished, user_message_id: undefined });
    expect(state.messages[0].serverId).toBeUndefined();
    expect(serverMessageId(state.messages[0])).toBeNull();
  });
});

describe("serverMessageId", () => {
  it("uses the stamped server id on a local message", () => {
    const state = started("hi");
    const message = { ...state.messages[0], serverId: "u9" };
    expect(serverMessageId(message)).toBe("u9");
  });

  it("is null for a local message the server has not acknowledged", () => {
    expect(serverMessageId(started("hi").messages[0])).toBeNull();
  });

  it("uses the message's own id when it came from the server", () => {
    const state: AssistantChatState = {
      ...initialAssistantChatState(),
      messages: mergeServerMessages(initialAssistantChatState(), [
        serverMessage({ id: "u1", role: "user", content: "hi" }),
        serverMessage({ id: "a1", content: "hello" }),
      ]).messages,
    };
    expect(serverMessageId(state.messages[0])).toBe("u1");
  });
});

describe("editTurn", () => {
  /** Finish a turn, so the state is one `editTurn` will actually accept. */
  function finished(state: AssistantChatState): AssistantChatState {
    return applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "a1",
      content: "ok",
      artifacts: [],
    });
  }

  it("truncates before the edited user message and hands back its text", () => {
    let state = started("Summarize it");
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "srv-1",
      content: "Here's the summary.",
      artifacts: [],
    });
    const userId = state.messages[0].id;

    const result = editTurn(state, userId);
    expect(result).not.toBeNull();
    expect(result?.prompt).toBe("Summarize it");
    // Everything from the edited message onward is gone, so the edited version
    // replaces it rather than being appended after it.
    expect(result?.state.messages).toEqual([]);
    expect(result?.state.streaming).toBe(false);
    expect(result?.state.error).toBeNull();
  });

  it("drops every turn after the edited one", () => {
    let state = started("first");
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "a1",
      content: "one",
      artifacts: [],
    });
    state = startAssistantTurn(state, "second");
    const secondUserId = state.messages[2].id;
    state = applyStreamEvent(state, {
      type: "done",
      conversation_id: "c1",
      message_id: "a2",
      content: "two",
      artifacts: [],
    });

    const result = editTurn(state, secondUserId);
    expect(result?.prompt).toBe("second");
    expect(result?.state.messages.map((m) => m.content)).toEqual(["first", "one"]);
  });

  it("keeps the attachments the original turn was sent with", () => {
    const attachments = [{ id: "f1", name: "resume.pdf", extension: "pdf" }];
    const state = finished(
      startAssistantTurn(initialAssistantChatState(), "convert this", attachments),
    );
    const result = editTurn(state, state.messages[0].id);
    expect(result?.attachments).toEqual(attachments);
    // A copy, not the same array, so re-sending cannot alias the old message.
    expect(result?.attachments).not.toBe(attachments);
  });

  it("returns null for an unknown id, an assistant id, or while streaming", () => {
    const state = finished(started("hi"));
    const assistantId = state.messages[1].id;
    expect(editTurn(state, "nope")).toBeNull();
    expect(editTurn(state, assistantId)).toBeNull();
    // Still streaming: the caller must stop first.
    expect(editTurn({ ...state, streaming: true }, state.messages[0].id)).toBeNull();
  });

  it("returns null for a blank message", () => {
    const state = finished(started("hi"));
    const blank: AssistantChatState = {
      ...state,
      messages: [{ ...state.messages[0], content: "   " }, state.messages[1]],
    };
    expect(editTurn(blank, state.messages[0].id)).toBeNull();
  });
});

describe("applyJobProgress", () => {
  function job(overrides: Partial<UiJob> = {}): UiJob {
    return {
      job_id: "j1",
      status: "PROCESSING",
      source_format: "pdf",
      target_format: "docx",
      input_file: "report.pdf",
      output_file: null,
      object_key: null,
      download_url: null,
      error_message: null,
      credits_used: 0,
      compute_duration_ms: 0,
      input_size_bytes: 0,
      output_size_bytes: 0,
      created_at: null,
      data_key_wrapped: null,
      client_encrypted: false,
      ...overrides,
    };
  }

  it("takes the status and percentage from the frame", () => {
    const next = applyJobProgress(job(), {
      job_id: "j1",
      status: "PROCESSING",
      progress: 42,
    });
    expect(next.status).toBe("PROCESSING");
    expect(next.progress).toBe(42);
  });

  it("fills in the terminal fields so no refetch is needed", () => {
    const next = applyJobProgress(job(), {
      job_id: "j1",
      status: "COMPLETED",
      credits_used: 7,
      compute_duration_ms: 1200,
      input_size_bytes: 10,
      output_size_bytes: 5,
    });
    expect(next.credits_used).toBe(7);
    expect(next.compute_duration_ms).toBe(1200);
    expect(next.output_size_bytes).toBe(5);
  });

  it("records the frame's message as the error text for a failure", () => {
    const next = applyJobProgress(job(), {
      job_id: "j1",
      status: "FAILED",
      message: "LibreOffice crashed",
    });
    expect(next.errorMessage).toBe("LibreOffice crashed");
  });

  it("never mistakes a non-failure message for an error", () => {
    const previous = job({ errorMessage: undefined });
    const next = applyJobProgress(previous, {
      job_id: "j1",
      status: "PROCESSING",
      message: "downloading input",
    });
    expect(next.errorMessage).toBeUndefined();
  });

  it("keeps the last known values when the frame omits them", () => {
    const next = applyJobProgress(job({ credits_used: 3, output_size_bytes: 9 }), {
      job_id: "j1",
      status: "PROCESSING",
    });
    expect(next.credits_used).toBe(3);
    expect(next.output_size_bytes).toBe(9);
  });
});
