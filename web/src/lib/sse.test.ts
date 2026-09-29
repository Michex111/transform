// Tests for the SSE frame parser.
//
// The network hands this parser arbitrary slices, so the cases that matter are
// the chunk boundaries: a frame split mid-field, a delimiter split across two
// chunks, and a stream that ends without a trailing blank line. The original
// inline reader in `api/client.ts` assumed each chunk ended on a frame boundary
// and silently dropped the tail.

import { describe, expect, it } from "vitest";
import { createSseParser } from "@/lib/sse";

describe("createSseParser", () => {
  it("parses a single complete frame", () => {
    const parser = createSseParser();
    expect(parser.push('event: delta\ndata: {"text":"hi"}\n\n')).toEqual([
      { event: "delta", data: '{"text":"hi"}' },
    ]);
  });

  it("buffers a frame split across chunks", () => {
    const parser = createSseParser();
    expect(parser.push("event: del")).toEqual([]);
    expect(parser.push('ta\ndata: {"text":"he')).toEqual([]);
    expect(parser.push('llo"}\n\n')).toEqual([{ event: "delta", data: '{"text":"hello"}' }]);
  });

  it("holds the delimiter when it lands across a chunk boundary", () => {
    const parser = createSseParser();
    expect(parser.push("data: one\n")).toEqual([]);
    expect(parser.push("\ndata: two\n\n")).toEqual([
      { event: "message", data: "one" },
      { event: "message", data: "two" },
    ]);
  });

  it("emits several frames from one chunk", () => {
    const parser = createSseParser();
    const frames = parser.push("event: a\ndata: 1\n\nevent: b\ndata: 2\n\n");
    expect(frames).toEqual([
      { event: "a", data: "1" },
      { event: "b", data: "2" },
    ]);
  });

  it("joins multiple data lines with a newline", () => {
    const parser = createSseParser();
    expect(parser.push("data: line one\ndata: line two\n\n")).toEqual([
      { event: "message", data: "line one\nline two" },
    ]);
  });

  it("ignores comment/heartbeat lines", () => {
    const parser = createSseParser();
    expect(parser.push(": keep-alive\n\n")).toEqual([]);
    expect(parser.push(": ping\ndata: real\n\n")).toEqual([{ event: "message", data: "real" }]);
  });

  it("defaults the event name to `message`", () => {
    const parser = createSseParser();
    expect(parser.push("data: x\n\n")).toEqual([{ event: "message", data: "x" }]);
  });

  it("strips exactly one leading space from a value", () => {
    const parser = createSseParser();
    expect(parser.push("data:  two\n\n")).toEqual([{ event: "message", data: " two" }]);
  });

  it("handles CRLF framing", () => {
    const parser = createSseParser();
    expect(parser.push("event: delta\r\ndata: 1\r\n\r\n")).toEqual([
      { event: "delta", data: "1" },
    ]);
  });

  it("flushes a final frame the stream never terminated", () => {
    const parser = createSseParser();
    expect(parser.push("event: delta\ndata: tail")).toEqual([]);
    expect(parser.flush()).toEqual([{ event: "delta", data: "tail" }]);
  });

  it("flushes nothing when only whitespace is buffered", () => {
    const parser = createSseParser();
    parser.push("\n\n");
    expect(parser.flush()).toEqual([]);
  });

  it("ignores id/retry fields it does not consume", () => {
    const parser = createSseParser();
    expect(parser.push("id: 7\nretry: 1000\nevent: delta\ndata: v\n\n")).toEqual([
      { event: "delta", data: "v" },
    ]);
  });

  it("does not emit a frame for a data-less event block", () => {
    const parser = createSseParser();
    expect(parser.push("event: nothing\n\n")).toEqual([]);
  });

  it("keeps frame order across interleaved pushes", () => {
    const parser = createSseParser();
    const seen = [
      ...parser.push("event: a\ndata: 1\n\nevent: b\nda"),
      ...parser.push('ta: 2\n\nevent: c\ndata: 3\n\n'),
    ];
    expect(seen.map((frame) => frame.event)).toEqual(["a", "b", "c"]);
  });
});
