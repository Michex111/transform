// Tests for the persisted-conversation transcript builder.
//
// This is where the reopened-conversation bugs were: the server stores a turn
// as a tool request, one raw-JSON `tool` row per call, then the answer. These
// cases pin that those rows fold back into exactly one assistant bubble, that
// the raw tool JSON never survives into `content`, and that legacy rows still
// read in plain language.

import { describe, expect, it } from "vitest";
import type { AssistantArtifact, AssistantMessage } from "@/api/types";
import { buildTranscript, toolStepLabel, toolStepSummary } from "@/lib/assistantTranscript";

function artifact(overrides: Partial<AssistantArtifact> = {}): AssistantArtifact {
  return { type: "file", id: "f1", name: "report.pdf", meta: null, ...overrides };
}

function user(content: string, overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return { id: "u1", role: "user", content, ...overrides };
}

function toolRequest(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: "req-1",
    role: "assistant",
    content: "",
    meta: { tool_calls: [{ id: "call-1", name: "list_files", arguments: {} }] },
    ...overrides,
  };
}

function toolRow(overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: "tool-1",
    role: "tool",
    // The raw tool result JSON the UI must never render.
    content: '{"matches":[{"id":"f1","name":"report.pdf"}],"count":1}',
    tool_name: "list_files",
    // `normalizeAssistantMessage` lifts these out of `meta`; the transcript
    // builder reads the typed fields, not the raw bag.
    label: "Looking through your files",
    summary: "Found 1 file",
    meta: { tool_call_id: "call-1" },
    ...overrides,
  };
}

function answer(content: string, overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return { id: "ans-1", role: "assistant", content, ...overrides };
}

describe("toolStepLabel", () => {
  it("prefers the persisted label", () => {
    expect(toolStepLabel("list_files", "Looking through your files")).toBe(
      "Looking through your files",
    );
  });

  it("falls back to a friendly phrase for a known tool", () => {
    expect(toolStepLabel("summarize_file")).toBe("Summarising the document");
  });

  it("falls back to a neutral line from the tool name for an unknown tool", () => {
    expect(toolStepLabel("custom_tool")).toBe("Used custom_tool");
  });

  it("never returns an empty string", () => {
    expect(toolStepLabel(null)).toBe("Used a tool");
    expect(toolStepLabel("")).toBe("Used a tool");
    expect(toolStepLabel(undefined, "   ")).toBe("Used a tool");
  });
});

describe("toolStepSummary", () => {
  it("trims and defaults to an empty string", () => {
    expect(toolStepSummary("  Found 4 files ")).toBe("Found 4 files");
    expect(toolStepSummary(undefined)).toBe("");
  });
});

describe("buildTranscript", () => {
  it("maps a user row to a user bubble", () => {
    const [message] = buildTranscript([user("Summarize my PDF")]);
    expect(message.role).toBe("user");
    expect(message.content).toBe("Summarize my PDF");
    expect(message.local).toBe(false);
  });

  it("collapses a turn into ONE assistant bubble carrying steps and the answer", () => {
    const transcript = buildTranscript([
      user("What's in my report?"),
      toolRequest(),
      toolRow(),
      answer("Your report is a short summary of Q3."),
    ]);

    expect(transcript).toHaveLength(2);
    const [userBubble, assistantBubble] = transcript;
    expect(userBubble.role).toBe("user");

    expect(assistantBubble.role).toBe("assistant");
    expect(assistantBubble.content).toBe("Your report is a short summary of Q3.");
    // The answer's id wins so a refetch can de-duplicate the streamed bubble.
    expect(assistantBubble.id).toBe("ans-1");
    expect(assistantBubble.steps).toEqual([
      { name: "list_files", label: "Looking through your files", summary: "Found 1 file" },
    ]);
  });

  it("never exposes the raw tool content", () => {
    const transcript = buildTranscript([
      user("q"),
      toolRequest(),
      toolRow(),
      answer("Here you go."),
    ]);
    const everything = transcript.map((message) => message.content).join("\n");
    expect(everything).not.toContain('"matches"');
    expect(everything).not.toContain("tool_call_id");
    expect(everything).not.toContain("list_files returned");
  });

  it("folds several tool rounds of one turn into the same bubble", () => {
    const transcript = buildTranscript([
      user("Convert it"),
      toolRequest({ id: "req-1" }),
      toolRow({ id: "tool-1", tool_name: "list_files" }),
      toolRequest({
        id: "req-2",
        meta: { tool_calls: [{ id: "call-2", name: "start_conversion", arguments: {} }] },
      }),
      toolRow({
        id: "tool-2",
        tool_name: "start_conversion",
        label: "Starting the conversion",
        summary: "Queued as job 7",
        meta: { tool_call_id: "call-2" },
      }),
      answer("It's converting now."),
    ]);

    const assistants = transcript.filter((message) => message.role === "assistant");
    expect(assistants).toHaveLength(1);
    expect(assistants[0].steps?.map((step) => step.name)).toEqual([
      "list_files",
      "start_conversion",
    ]);
    expect(assistants[0].content).toBe("It's converting now.");
  });

  it("renders a legacy tool row with a friendly fallback and no summary", () => {
    const transcript = buildTranscript([
      user("q"),
      toolRequest(),
      toolRow({ label: undefined, summary: undefined }),
      answer("Done."),
    ]);
    expect(transcript[1].steps).toEqual([
      { name: "list_files", label: "Looking through your files", summary: "" },
    ]);
  });

  it("drops empty assistant plumbing with no answer, steps or artifacts", () => {
    const transcript = buildTranscript([user("q"), toolRequest(), answer("")]);
    expect(transcript).toHaveLength(1);
    expect(transcript[0].role).toBe("user");
  });

  it("keeps a standalone answer that no tool request preceded", () => {
    const transcript = buildTranscript([user("Which format for a resume?"), answer("PDF, usually.")]);
    expect(transcript.map((message) => message.content)).toEqual([
      "Which format for a resume?",
      "PDF, usually.",
    ]);
  });

  it("merges artifacts from the tool rows and the final answer, de-duplicated", () => {
    const transcript = buildTranscript([
      user("q"),
      toolRequest(),
      toolRow({ artifacts: [artifact({ id: "f1" })] }),
      answer("Here.", { artifacts: [artifact({ id: "f2", name: "second.pdf" })] }),
    ]);
    expect(transcript[1].artifacts.map((item) => item.id)).toEqual(["f1", "f2"]);
  });

  it("keeps a turn that has steps but no answer text (no empty bubble)", () => {
    const transcript = buildTranscript([user("q"), toolRequest(), toolRow()]);
    expect(transcript).toHaveLength(2);
    expect(transcript[1].role).toBe("assistant");
    expect(transcript[1].content).toBe("");
    expect(transcript[1].steps).toHaveLength(1);
    expect(transcript[1].artifacts).toEqual([]);
  });

  it("uses the model's preamble only when the turn has no real answer", () => {
    const transcript = buildTranscript([
      user("q"),
      toolRequest({ content: "Let me check your files." }),
      toolRow(),
    ]);
    expect(transcript[1].content).toBe("Let me check your files.");
  });

  it("prefers the final answer over the preamble", () => {
    const transcript = buildTranscript([
      user("q"),
      toolRequest({ content: "Let me check your files." }),
      toolRow(),
      answer("Found one file: report.pdf."),
    ]);
    expect(transcript[1].content).toBe("Found one file: report.pdf.");
  });

  it("preserves server order across several turns", () => {
    const transcript = buildTranscript([
      user("first", { id: "u1" }),
      toolRequest({ id: "req-1" }),
      toolRow({ id: "t1" }),
      answer("one", { id: "a1" }),
      user("second", { id: "u2" }),
      answer("two", { id: "a2" }),
    ]);
    expect(transcript.map((message) => message.content)).toEqual([
      "first",
      "one",
      "second",
      "two",
    ]);
  });

  it("survives an empty conversation", () => {
    expect(buildTranscript([])).toEqual([]);
  });
});
