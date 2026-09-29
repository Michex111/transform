// A dependency-free parser for the `text/event-stream` framing.
//
// The repo already reads SSE in `api/client.ts#subscribeToJobStream`, but that
// reader is wired to the job endpoints and splits on `\n\n` inline. The
// assistant chat is a **POST** whose response body is streamed, so it cannot use
// `EventSource` (which only issues GETs and cannot carry an Authorization
// header) — it needs a reader over `fetch` + `response.body.getReader()`.
//
// Keeping the framing logic pure (and tested with awkward chunk boundaries)
// matters because the network hands it arbitrary slices: a single frame can
// arrive in three chunks, and a chunk can end mid-field. Everything below the
// `createSseParser` boundary is string-in / frames-out, so it runs in the
// repo's Node test environment with no DOM.

/** One parsed SSE event: its `event:` name and its joined `data:` payload. */
export interface SseFrame {
  event: string;
  data: string;
}

export interface SseParser {
  /** Feed a decoded chunk; returns every frame that is now complete. */
  push(chunk: string): SseFrame[];
  /** Parse whatever is left in the buffer (a stream that ended without `\n\n`). */
  flush(): SseFrame[];
}

/**
 * Parse one `\n`-separated block into a frame, or `null` when it carries no
 * `data:` field (a bare comment/heartbeat, or the blank leading block).
 *
 * Per the SSE spec a field is `name: value`; a leading space in the value is
 * stripped, a line beginning with `:` is a comment, and several `data:` lines in
 * one event are joined with `\n`.
 */
function parseBlock(block: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
    // `id`, `retry`, and unknown fields are deliberately ignored: nothing in
    // this app reconnects by last-event-id, and Chrome's ~3s auto-reconnect
    // does not apply to a reader over `fetch`.
  }

  // A frame with no data is not an event — it is a comment or a stray blank.
  return dataLines.length ? { event, data: dataLines.join("\n") } : null;
}

/**
 * A stateful SSE frame parser.
 *
 * Stateful rather than a plain function because the split point between frames
 * is not known until it arrives: `push` accumulates until it sees a blank line,
 * and keeps the trailing partial block for the next call.
 */
export function createSseParser(): SseParser {
  let buffer = "";

  function drain(final: boolean): SseFrame[] {
    // Normalise CRLF/CR to LF first, so a `\r\n\r\n` delimiter and a `\r\n`
    // field separator are handled by the same `\n`-based logic (and a `\r` left
    // mid-buffer by a split chunk cannot break the delimiter match).
    buffer = buffer.replace(/\r\n?/g, "\n");

    const frames: SseFrame[] = [];
    let delimiter = buffer.indexOf("\n\n");

    while (delimiter !== -1) {
      const block = buffer.slice(0, delimiter);
      buffer = buffer.slice(delimiter + 2);
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
      delimiter = buffer.indexOf("\n\n");
    }

    // On flush the stream is over, so the remainder is a complete (if
    // unterminated) frame rather than a partial one.
    if (final && buffer.trim()) {
      const frame = parseBlock(buffer);
      if (frame) frames.push(frame);
      buffer = "";
    }

    return frames;
  }

  return {
    push: (chunk: string) => {
      buffer += chunk;
      return drain(false);
    },
    flush: () => drain(true),
  };
}

/**
 * Read a streamed `Response` and hand each parsed frame to `onFrame`.
 *
 * Resolves when the body ends. The caller owns cancellation by aborting the
 * `fetch` that produced `response` (the reader then rejects with an
 * `AbortError`), which is what the chat's Stop button does.
 */
export async function readSseStream(
  response: Response,
  onFrame: (frame: SseFrame) => void,
): Promise<void> {
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    // `stream: true` so a multi-byte character split across two chunks is held
    // back until it is complete instead of being decoded as a replacement char.
    for (const frame of parser.push(decoder.decode(value, { stream: true }))) onFrame(frame);
  }

  // Flush the decoder, then the parser: a stream that closed without a trailing
  // blank line still has one final frame buffered.
  for (const frame of parser.push(decoder.decode())) onFrame(frame);
  for (const frame of parser.flush()) onFrame(frame);
}
