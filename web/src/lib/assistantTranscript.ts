// Turn assembly for a *persisted* conversation.
//
// A stored turn is not a list of chat bubbles. The server records, in order:
//
//   1. the user's message;
//   2. an assistant "tool request" row — often with EMPTY content, whose
//      `meta.tool_calls` names the tools the model asked for;
//   3. one `tool` row per call, whose `content` is the raw tool-result JSON and
//      whose `meta` carries the friendly `label`/`summary`/`artifacts`;
//   4. finally the assistant's answer, whose `meta.artifacts` repeats any files
//      or jobs it surfaced.
//
// Rendering those rows verbatim is what showed raw JSON in the transcript and
// produced an empty assistant bubble per tool round. `buildTranscript` folds
// them back into the shape a person actually reads: exactly one assistant
// bubble per turn, carrying the steps it took and the answer it gave. Tool
// `content` is never exposed — only the curated label and summary are.
//
// Pure and dependency-light so it can be tested in the Node environment, and so
// `mergeServerMessages` can reuse it as the single server-shaping code path.

import type { AssistantArtifact, AssistantMessage } from "@/api/types";
import {
  mergeArtifacts,
  type AssistantChatMessage,
  type AssistantStep,
} from "@/lib/assistantChat";

/**
 * Friendly phrasing for a tool whose persisted row predates `meta.label`.
 *
 * This mirrors the server's own `_TOOL_LABELS` so a legacy conversation reads
 * the same as a fresh one. It is a *fallback only*: a persisted `meta.label`
 * always wins, and an unknown tool name degrades to `Used <name>` rather than
 * to the raw tool result.
 */
const TOOL_LABELS: Record<string, string> = {
  list_files: "Looking through your files",
  list_folders: "Looking through your folders",
  get_file_info: "Checking that file",
  list_supported_targets: "Checking the supported formats",
  read_file_text: "Reading the document",
  summarize_file: "Summarising the document",
  start_conversion: "Starting the conversion",
  get_conversion_status: "Checking the conversion",
  list_recent_conversions: "Looking at your recent conversions",
  create_folder: "Creating the folder",
  move_file: "Moving the file",
};

/**
 * Human label for one tool step.
 *
 * The persisted `meta.label` wins. Otherwise a known tool gets its friendly
 * phrase, and anything else falls back to a neutral line built from the tool
 * name (`Used list_files`). It never returns the tool's JSON `content` and
 * never an empty string, so a legacy row can't render as a blank line.
 */
export function toolStepLabel(name: string | null | undefined, label?: string | null): string {
  const friendly = (label ?? "").trim();
  if (friendly) return friendly;
  const tool = (name ?? "").trim();
  if (!tool) return "Used a tool";
  return TOOL_LABELS[tool] ?? `Used ${tool}`;
}

/** A trimmed summary, or `""` when the row has none (rendered as no second line). */
export function toolStepSummary(summary?: string | null): string {
  return (summary ?? "").trim();
}

/** True when an assistant row is a tool *request* rather than a final answer. */
function hasToolCalls(message: AssistantMessage): boolean {
  const calls = message.meta?.tool_calls;
  return Array.isArray(calls) && calls.length > 0;
}

/** An assistant message with no tool step, used for a plain answer in a turn. */
function plainAssistantMessage(message: AssistantMessage): AssistantChatMessage {
  const artifacts = mergeArtifacts([], message.artifacts ?? []);
  const out: AssistantChatMessage = {
    id: message.id,
    role: "assistant",
    content: message.content,
    createdAt: message.created_at ?? null,
    toolName: null,
    artifacts,
    attachments: [],
    pending: false,
    local: false,
  };
  return out;
}

/** A conversation turn being reconstructed from the server's rows. */
interface Turn {
  id: string;
  createdAt: string | null;
  /** The final answer's text, when the server recorded one. */
  content: string;
  /**
   * The model's preamble before it called a tool. Kept only as a fallback so a
   * turn truncated mid-tool still says something rather than going blank — it
   * is never used once a real answer exists.
   */
  fallback: string;
  steps: AssistantStep[];
  artifacts: AssistantArtifact[];
}

function startTurn(id: string, createdAt: string | null): Turn {
  return { id, createdAt, content: "", fallback: "", steps: [], artifacts: [] };
}

/**
 * Rebuild the reader-facing transcript from the server's persisted messages.
 *
 * Rules:
 *   - a `user` row becomes a user bubble;
 *   - an assistant tool request plus every `tool` row that follows it collapse
 *     into ONE assistant bubble carrying `steps` and the union of their
 *     artifacts (a turn may contain several tool rounds — they share one bubble);
 *   - the following final assistant message (non-empty content, no
 *     `meta.tool_calls`) contributes that turn's answer text and artifacts;
 *   - assistant plumbing rows with no steps, no artifacts and no text are
 *     dropped rather than rendered as an empty bubble;
 *   - server order is preserved and tool `content` is never exposed.
 */
export function buildTranscript(messages: AssistantMessage[]): AssistantChatMessage[] {
  const out: AssistantChatMessage[] = [];
  let turn: Turn | null = null;

  function flushTurn(): void {
    if (!turn) return;
    const content = turn.content.trim() ? turn.content : turn.fallback;
    const { steps, artifacts } = turn;
    if (content || steps.length > 0 || artifacts.length > 0) {
      const message: AssistantChatMessage = {
        id: turn.id,
        role: "assistant",
        content,
        createdAt: turn.createdAt,
        toolName: null,
        artifacts,
        attachments: [],
        pending: false,
        local: false,
      };
      if (steps.length > 0) message.steps = steps;
      out.push(message);
    }
    turn = null;
  }

  messages.forEach((message, index) => {
    if (message.role === "user") {
      flushTurn();
      out.push({
        id: message.id || `user-${index}`,
        role: "user",
        content: message.content,
        createdAt: message.created_at ?? null,
        toolName: null,
        artifacts: [],
        // A persisted user row carries its attachments in `meta.attachments`;
        // this is what makes a reopened conversation show its chips.
        attachments: message.attachments ?? [],
        pending: false,
        local: false,
      });
      return;
    }

    if (message.role === "tool") {
      // A tool result always belongs to the current turn. If the request row was
      // not persisted (or is unrecognisable), the result still starts a turn so
      // its step is not lost.
      if (!turn) turn = startTurn(message.id || `turn-${index}`, message.created_at ?? null);
      turn.steps.push({
        name: message.tool_name ?? "",
        label: toolStepLabel(message.tool_name, message.label),
        summary: toolStepSummary(message.summary),
      });
      if (message.artifacts?.length) {
        turn.artifacts = mergeArtifacts(turn.artifacts, message.artifacts);
      }
      return;
    }

    // An assistant row that asked for tools is plumbing: it opens (or continues)
    // the current turn but is never rendered on its own.
    if (hasToolCalls(message)) {
      if (!turn) turn = startTurn(message.id || `turn-${index}`, message.created_at ?? null);
      if (message.content.trim()) turn.fallback = message.content;
      return;
    }

    // A plain assistant row. If a turn is open it is that turn's answer and ends
    // it; otherwise it is a standalone answer (e.g. a general format question).
    if (turn) {
      if (message.content.trim()) turn.content = message.content;
      if (message.artifacts?.length) {
        turn.artifacts = mergeArtifacts(turn.artifacts, message.artifacts);
      }
      // Prefer the answer's id so a refetch de-duplicates the optimistic bubble,
      // which the stream finalised with this same id.
      if (message.id) turn.id = message.id;
      flushTurn();
      return;
    }

    if (message.content || (message.artifacts?.length ?? 0) > 0) {
      const answer = plainAssistantMessage(message);
      if (!answer.id) answer.id = `assistant-${index}`;
      out.push(answer);
    }
  });

  flushTurn();
  return out;
}
