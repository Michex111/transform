// The pure state machine behind a Transform AI conversation.
//
// The assistant page owns effects, focus, and fetching; everything about *what
// a stream of events does to the transcript* lives here so it can be tested in
// the Node environment without rendering anything. `applyStreamEvent` is a pure
// `(state, event) => state` reducer — no mutation, no ids read from the clock
// beyond the local sequence counter used to key optimistic messages.

import {
  ASSISTANT_NOT_AVAILABLE_FOR_TIER,
  ASSISTANT_QUOTA_EXCEEDED,
  type AssistantArtifact,
  type AssistantAttachment,
  type AssistantMessage,
  type AssistantStreamEvent,
  type JobProgressEvent,
} from "@/api/types";
import { buildTranscript } from "@/lib/assistantTranscript";
import type { UiJob } from "@/jobs/jobStore";

/** One entry of the "what the assistant is doing" row while a turn streams. */
export interface AssistantToolActivity {
  name: string;
  label: string;
  status: "running" | "done";
  summary?: string;
}

/**
 * One tool call a persisted turn made, as shown in its steps disclosure.
 *
 * `label` is always a human phrase (never a tool name, never JSON), and
 * `summary` is `""` when the server recorded none, so a legacy row cannot
 * render as a blank line.
 */
export interface AssistantStep {
  name: string;
  label: string;
  summary: string;
}

export interface AssistantChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  createdAt: string | null;
  toolName: string | null;
  artifacts: AssistantArtifact[];
  /**
   * Files the user attached to this turn. Only ever non-empty on a `user`
   * message; carried here (not on the composer) so a sent turn keeps its chips
   * in the transcript even after the composer is cleared.
   */
  attachments: AssistantAttachment[];
  /**
   * The tool calls this turn made, as a disclosure above the answer.
   *
   * Populated two ways, and both must look identical: `buildTranscript` folds
   * them in for a *reopened* turn, and the reducer copies the live
   * `AssistantChatState.tools` onto the message when the turn ends. Without the
   * second path the "thought process" was visible while streaming and then
   * vanished the moment the answer finished — so a turn read differently
   * depending on whether it had just happened or been reloaded.
   */
  steps?: AssistantStep[];
  /** True while this assistant turn is still receiving deltas. */
  pending: boolean;
  /**
   * The server's id for this message, once it has one.
   *
   * A message sent *in this session* starts life with a local `user-N` id;
   * the `done` frame's `user_message_id` is the only thing that turns it into
   * a real server row without a reload, and the edit flow needs that id to
   * truncate the persisted transcript. A message loaded *from* the server
   * already carries its server id as `id` (`local` is false), so this stays
   * unset for those — read it through `serverMessageId` rather than directly.
   */
  serverId?: string;
  /**
   * True for a message this tab created optimistically, before the server
   * confirmed it. A refetch mid-turn must not erase the turn in progress, so
   * `mergeServerMessages` keeps local messages the server does not know yet.
   */
  local: boolean;
}

export interface AssistantChatError {
  code: string;
  message: string;
}

export interface AssistantChatState {
  conversationId: string | null;
  messages: AssistantChatMessage[];
  /** Tool activity for the turn in flight; cleared when a new turn starts. */
  tools: AssistantToolActivity[];
  stage: string | null;
  streaming: boolean;
  error: AssistantChatError | null;
}

/** Distinct local message ids without depending on `crypto.randomUUID`. */
let localSeq = 0;
function localId(prefix: string): string {
  localSeq += 1;
  return `${prefix}-${localSeq}`;
}

export function initialAssistantChatState(): AssistantChatState {
  return {
    conversationId: null,
    messages: [],
    tools: [],
    stage: null,
    streaming: false,
    error: null,
  };
}

function artifactKey(artifact: AssistantArtifact): string {
  return `${artifact.type}:${artifact.id}`;
}

/**
 * Union two artifact lists, incoming winning, preserving first-seen order.
 *
 * Deduplication is by `type:id` because the same file can legitimately arrive
 * twice within one turn — once from a `tool` frame's `artifacts` array and once
 * as its own `artifact` frame — and rendering two identical chips reads as two
 * different files.
 */
export function mergeArtifacts(
  existing: AssistantArtifact[],
  incoming: AssistantArtifact[],
): AssistantArtifact[] {
  const merged = [...existing];
  const index = new Map(merged.map((artifact, i) => [artifactKey(artifact), i]));

  for (const artifact of incoming) {
    const key = artifactKey(artifact);
    const at = index.get(key);
    if (at === undefined) {
      index.set(key, merged.length);
      merged.push(artifact);
    } else {
      merged[at] = artifact;
    }
  }

  return merged;
}

/**
 * Begin a turn: append the user's message and an empty assistant placeholder.
 *
 * The placeholder exists from the first tick so a delta has somewhere to land
 * and so the UI can show "streaming" without inventing a second code path.
 * Blank input is a no-op (the composer already blocks it) — an empty bubble
 * would be a UI bug, not a message.
 */
export function startAssistantTurn(
  state: AssistantChatState,
  text: string,
  attachments: AssistantAttachment[] = [],
): AssistantChatState {
  const content = text.trim();
  if (!content) return state;

  const user: AssistantChatMessage = {
    id: localId("user"),
    role: "user",
    content,
    createdAt: new Date().toISOString(),
    toolName: null,
    artifacts: [],
    // Copied so a later edit of the composer's list cannot mutate a message
    // already in the transcript.
    attachments: attachments.map((attachment) => ({ ...attachment })),
    pending: false,
    local: true,
  };
  const placeholder: AssistantChatMessage = {
    id: localId("assistant"),
    role: "assistant",
    content: "",
    createdAt: new Date().toISOString(),
    toolName: null,
    artifacts: [],
    attachments: [],
    pending: true,
    local: true,
  };

  return {
    ...state,
    messages: [...state.messages, user, placeholder],
    tools: [],
    stage: null,
    streaming: true,
    error: null,
  };
}

/** Index of the last assistant message still receiving deltas, or `-1`. */
function pendingAssistantIndex(messages: AssistantChatMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (message.role === "assistant" && message.pending) return i;
  }
  return -1;
}

/**
 * Turn the turn's finished tool activity into the message's steps disclosure.
 *
 * Returns `undefined` (not an empty array) when nothing ran, so a plain answer
 * carries no disclosure at all rather than an empty one. Only `done` tools are
 * included: a tool still `running` at the end of a turn is one the server never
 * reported finishing, and showing it would imply an outcome we do not know.
 */
function stepsFromTools(tools: AssistantToolActivity[]): AssistantStep[] | undefined {
  const steps = tools
    .filter((tool) => tool.status === "done")
    .map((tool) => ({ name: tool.name, label: tool.label, summary: tool.summary ?? "" }));
  return steps.length > 0 ? steps : undefined;
}

/**
 * Apply one stream event to the transcript.
 *
 * Ordering guarantees the reducer relies on:
 *   - `delta` frames append to the pending assistant message; a delta with no
 *     pending message is dropped rather than resurrecting a finished turn.
 *   - `done` is authoritative: it replaces the streamed text with the server's
 *     final `content` and, when the frame carries artifacts, replaces the
 *     accumulated list (otherwise the accumulated one is kept).
 *   - `error` stops the stream and keeps whatever text already arrived, so the
 *     user does not lose a partial answer.
 */
export function applyStreamEvent(
  state: AssistantChatState,
  event: AssistantStreamEvent,
): AssistantChatState {
  switch (event.type) {
    case "status":
      return { ...state, stage: event.stage };

    case "delta": {
      const at = pendingAssistantIndex(state.messages);
      if (at === -1 || !event.text) return state;
      const messages = state.messages.slice();
      messages[at] = { ...messages[at], content: messages[at].content + event.text };
      return { ...state, messages };
    }

    case "tool": {
      const tools = state.tools.slice();
      const at = tools.findIndex((tool) => tool.name === event.tool.name);
      const done = event.tool.status === "done";
      const previous = at === -1 ? undefined : tools[at];
      const label = event.tool.label || previous?.label || event.tool.name;

      const next: AssistantToolActivity = {
        name: event.tool.name,
        label,
        status: done ? "done" : "running",
      };
      if (done && event.tool.summary) next.summary = event.tool.summary;
      else if (!done && previous?.summary) next.summary = previous.summary;

      if (at === -1) tools.push(next);
      else tools[at] = next;

      let messages = state.messages;
      if (done && event.tool.artifacts?.length) {
        const index = pendingAssistantIndex(state.messages);
        if (index !== -1) {
          messages = state.messages.slice();
          messages[index] = {
            ...messages[index],
            artifacts: mergeArtifacts(messages[index].artifacts, event.tool.artifacts),
          };
        }
      }

      return { ...state, tools, messages };
    }

    case "artifact": {
      const at = pendingAssistantIndex(state.messages);
      if (at === -1) return state;
      const messages = state.messages.slice();
      messages[at] = {
        ...messages[at],
        artifacts: mergeArtifacts(messages[at].artifacts, [event.artifact]),
      };
      return { ...state, messages };
    }

    case "done": {
      const artifacts = event.artifacts.length ? event.artifacts : undefined;
      const at = pendingAssistantIndex(state.messages);
      const messages = state.messages.slice();
      // Carry the live tool activity onto the finished message so the steps
      // disclosure survives completion (see `AssistantChatMessage.steps`).
      const steps = stepsFromTools(state.tools);

      if (at === -1) {
        // A tool-only answer can finish without ever emitting a delta.
        messages.push({
          id: event.message_id || localId("assistant"),
          role: "assistant",
          content: event.content,
          createdAt: new Date().toISOString(),
          toolName: null,
          artifacts: event.artifacts,
          attachments: [],
          ...(steps ? { steps } : {}),
          pending: false,
          local: !event.message_id,
        });
      } else {
        messages[at] = {
          ...messages[at],
          id: event.message_id || messages[at].id,
          content: event.content,
          artifacts: artifacts ?? messages[at].artifacts,
          // Keep the transcript's steps when the event carries none, but never
          // overwrite them with `undefined`.
          ...(steps ? { steps } : {}),
          pending: false,
          local: event.message_id ? false : messages[at].local,
        };
      }

      // Stamp the turn's USER message with the server's id for it, when the
      // frame carries one. Walking back from the answer (rather than assuming
      // the row immediately before it) keeps this correct for a tool-only
      // answer and for a turn whose request rows sit between the two.
      const userId = event.user_message_id;
      if (userId) {
        const answerAt = at === -1 ? messages.length - 1 : at;
        for (let i = answerAt - 1; i >= 0; i--) {
          if (messages[i].role === "user") {
            messages[i] = { ...messages[i], serverId: userId };
            break;
          }
        }
      }

      return {
        ...state,
        conversationId: event.conversation_id || state.conversationId,
        messages,
        tools: state.tools.map((tool) => ({ ...tool, status: "done" as const })),
        stage: null,
        streaming: false,
      };
    }

    case "error": {
      const messages = state.messages.slice();
      const at = pendingAssistantIndex(state.messages);
      // Stop the spinner on the partial bubble but keep its text: a failed turn
      // that already said something should not blank out what it said. The tool
      // steps it managed to run are kept for the same reason — they explain how
      // far it got before failing.
      const steps = stepsFromTools(state.tools);
      if (at !== -1) {
        messages[at] = {
          ...messages[at],
          pending: false,
          ...(steps ? { steps } : {}),
        };
      }
      return {
        ...state,
        messages,
        stage: null,
        streaming: false,
        error: { code: event.code, message: event.message },
      };
    }
  }
}

/**
 * Replace the transcript with the server's copy, keeping optimistic messages
 * the server has not acknowledged (an in-flight turn, or a message just sent).
 *
 * `buildTranscript` does the shaping: a persisted turn (tool request + tool
 * rows + final answer) becomes one assistant bubble, so a reopened conversation
 * reads the same as a live one instead of showing raw tool JSON. The server is
 * the source of truth for history, so its bubbles come first and in its order;
 * only local messages whose id the server did not return survive, appended in
 * their original order.
 */
export function mergeServerMessages(
  state: AssistantChatState,
  serverMessages: AssistantMessage[],
): AssistantChatState {
  const server = buildTranscript(serverMessages);
  const serverIds = new Set(server.map((message) => message.id));
  const localOnly = state.messages.filter(
    (message) => message.local && !serverIds.has(message.id),
  );

  return {
    ...state,
    messages: [...server, ...localOnly],
    error: null,
  };
}

/**
 * A short, human title derived from the first thing the user said.
 *
 * Titles are shown in a narrow rail, so they are collapsed to one line and
 * clipped at a word boundary — a hard truncation that cuts mid-word ("Summarize
 * my latest re…") reads as a rendering bug rather than an abbreviation.
 */
export function conversationTitle(text: string, maxLength = 48): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  if (!collapsed) return "New chat";
  if (collapsed.length <= maxLength) return collapsed;

  const clipped = collapsed.slice(0, maxLength);
  const lastSpace = clipped.lastIndexOf(" ");
  const base = lastSpace > maxLength * 0.6 ? clipped.slice(0, lastSpace) : clipped;
  return `${base.trimEnd()}…`;
}

/** Copy for the activity row, keyed by the `status` frame's stage. */
export function stageLabel(stage: string | null): string {
  switch (stage) {
    case "thinking":
      return "Thinking…";
    case "searching":
    case "tools":
      return "Looking through your files…";
    case "writing":
      return "Writing a reply…";
    default:
      return "Working…";
  }
}

/**
 * End a turn the user stopped.
 *
 * Distinct from an `error` event on purpose: the request was cancelled, not
 * refused, so the transcript keeps the partial answer and shows no error — the
 * only visible change is that the streaming affordances stop.
 */
export function stopAssistantTurn(state: AssistantChatState): AssistantChatState {
  // A stopped turn keeps whatever the assistant had said AND whatever it had
  // done, so the user can see how far it got before they cut it off.
  const steps = stepsFromTools(state.tools);
  return {
    ...state,
    streaming: false,
    stage: null,
    messages: state.messages.map((message) =>
      message.pending
        ? { ...message, pending: false, ...(steps ? { steps } : {}) }
        : message,
    ),
    tools: state.tools.map((tool) => ({ ...tool, status: "done" as const })),
  };
}

/**
 * Drop the turn that just failed, so `Try again` can re-ask without leaving a
 * duplicate question behind.
 *
 * Only *local* (unconfirmed) messages are removed: a turn loaded from the
 * server is history, and the retry banner is only ever shown for a failure that
 * happened in this tab.
 */
export function undoLastTurn(state: AssistantChatState): AssistantChatState {
  const messages = state.messages.slice();

  const last = messages[messages.length - 1];
  if (last && last.role === "assistant" && last.local) messages.pop();

  const previous = messages[messages.length - 1];
  if (previous && previous.role === "user" && previous.local) messages.pop();

  return { ...state, messages, streaming: false, stage: null, tools: [], error: null };
}

/** What a regenerate needs to re-run a turn. */
export interface RegenerateTurn {
  /** The transcript with the target turn (and anything after it) removed. */
  state: AssistantChatState;
  /** The user message that produced the answer being regenerated. */
  prompt: string;
  /** The attachments that turn was sent with, so a retry keeps its files. */
  attachments: AssistantAttachment[];
}

/**
 * Prepare to regenerate the assistant answer with `messageId`.
 *
 * Truncates the transcript back to the user message that produced that answer
 * (removing it and every later message) and hands back the prompt, so the
 * caller can call `startAssistantTurn` again and re-stream. Truncating rather
 * than deleting just the one bubble keeps the transcript a valid alternating
 * history: a later answer would otherwise still be replying to a question the
 * regenerated turn no longer asks.
 *
 * Returns `null` when `messageId` is not an assistant message, or when no user
 * message precedes it — there is nothing honest to re-ask in either case. A
 * still-streaming turn is not regenerable (the caller must stop it first).
 */
export function regenerateTurn(
  state: AssistantChatState,
  messageId: string,
): RegenerateTurn | null {
  if (state.streaming) return null;

  const answerIndex = state.messages.findIndex(
    (message) => message.id === messageId && message.role === "assistant",
  );
  if (answerIndex === -1) return null;

  let userIndex = -1;
  for (let i = answerIndex - 1; i >= 0; i--) {
    if (state.messages[i].role === "user") {
      userIndex = i;
      break;
    }
  }
  if (userIndex === -1) return null;

  const prompt = state.messages[userIndex].content;
  if (!prompt.trim()) return null;

  return {
    state: {
      ...state,
      messages: state.messages.slice(0, userIndex),
      tools: [],
      stage: null,
      streaming: false,
      error: null,
    },
    prompt,
    attachments: state.messages[userIndex].attachments.map((attachment) => ({ ...attachment })),
  };
}

/**
 * The server's id for a transcript message, or `null` when it has none.
 *
 * Two sources, one question. A message loaded from a persisted conversation
 * *is* its server row, so its `id` is the server id; a message sent in this
 * session only learns its server id from a `done` frame's `user_message_id`
 * (see `AssistantChatMessage.serverId`). Reading both here keeps every caller
 * from having to know which kind of message it is holding.
 */
export function serverMessageId(message: AssistantChatMessage): string | null {
  if (message.serverId) return message.serverId;
  return message.local ? null : message.id || null;
}

/** What an edit needs to replace a turn. */
export interface EditTurn {
  /** The transcript with the edited turn (and anything after it) removed. */
  state: AssistantChatState;
  /** The text the edited turn originally carried, for the editor's prefill. */
  prompt: string;
  /** The attachments that turn was sent with, so the editor can prefill those. */
  attachments: AssistantAttachment[];
}

/**
 * Prepare to edit the user message with `messageId` and resend it.
 *
 * Truncates the transcript to everything *before* the edited message, so the
 * edited version replaces it and every later turn is dropped. That is the only
 * coherent history: the following turns were answers to a question the user has
 * now changed, and leaving them would make the assistant's replies respond to
 * something that is no longer in the transcript.
 *
 * Returns `null` when `messageId` is not a user message (an unknown id, an
 * assistant id) or when the turn is still streaming — the caller must stop it
 * first, exactly as with `regenerateTurn`. A blank prompt is also refused: the
 * UI blocks an empty save, and a whitespace-only message has nothing to re-ask.
 */
export function editTurn(state: AssistantChatState, messageId: string): EditTurn | null {
  if (state.streaming) return null;

  const index = state.messages.findIndex(
    (message) => message.id === messageId && message.role === "user",
  );
  if (index === -1) return null;

  const prompt = state.messages[index].content;
  if (!prompt.trim()) return null;

  return {
    state: {
      ...state,
      messages: state.messages.slice(0, index),
      tools: [],
      stage: null,
      streaming: false,
      error: null,
    },
    prompt,
    attachments: state.messages[index].attachments.map((attachment) => ({ ...attachment })),
  };
}

/**
 * Fold one job progress frame into a job's client-side state.
 *
 * The `ConversionCard` inside a transcript subscribes to the same SSE stream
 * the Queue does, so it has to apply exactly the same mapping — otherwise the
 * same job would read differently depending on where the user looked at it.
 * `message` is the only field with a rule: it carries status prose ("downloading
 * file", the failure reason), so it becomes the error text for a `FAILED` event
 * and is ignored otherwise. It must never be mistaken for a filename.
 */
export function applyJobProgress(job: UiJob, event: JobProgressEvent): UiJob {
  return {
    ...job,
    status: event.status,
    progress: event.progress,
    errorMessage:
      event.status === "FAILED" ? event.message ?? job.errorMessage : job.errorMessage,
    // Only the terminal event carries these; spreading them in means the card
    // is complete the moment the job finishes, without waiting for a refetch.
    credits_used: event.credits_used ?? job.credits_used,
    compute_duration_ms: event.compute_duration_ms ?? job.compute_duration_ms,
    input_size_bytes: event.input_size_bytes ?? job.input_size_bytes,
    output_size_bytes: event.output_size_bytes ?? job.output_size_bytes,
  };
}

/**
 * How the UI talks about a failed assistant request.
 *
 * `kind` is the branch the caller takes (a toast, a full panel, or an inline
 * retry); `toast` is the one-line version and `detail` the expanded one. The
 * three codes are the ones the frozen contract names, and anything else falls
 * through to the server's own message rather than a generic apology, because
 * only the known codes have copy this app can honestly promise.
 */
export interface AssistantErrorCopy {
  kind: "quota" | "tier" | "generic";
  title: string;
  detail: string;
  toast: string;
}

export function assistantErrorCopy(code: string, message: string): AssistantErrorCopy {
  switch (code) {
    case ASSISTANT_QUOTA_EXCEEDED:
      return {
        kind: "quota",
        title: "Hourly AI limit reached",
        detail: "You've used every AI request for this hour. It resets automatically.",
        toast: "Hourly AI limit reached — try again later",
      };
    case ASSISTANT_NOT_AVAILABLE_FOR_TIER:
      return {
        kind: "tier",
        title: "Transform AI isn't available on your plan",
        detail:
          "Your current plan doesn't include the assistant. Upgrading adds it — standard conversions keep working either way.",
        toast: "Transform AI isn't available on your plan",
      };
    default:
      return {
        kind: "generic",
        title: "The assistant couldn't answer",
        detail: message || "Something went wrong on the way to the assistant. Try again.",
        toast: message || "The assistant couldn't answer",
      };
  }
}
