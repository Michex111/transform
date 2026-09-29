// Tests for the dependency-free Markdown subset.
//
// The rules that matter to the product: the constructs the assistant is told to
// use are actually understood, and — because a streaming answer is often read
// mid-marker — an unmatched marker stays literal rather than swallowing text.

import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "@/lib/assistantMarkdown";

describe("parseInline", () => {
  it("returns plain text unchanged", () => {
    expect(parseInline("just words")).toEqual([{ type: "text", value: "just words" }]);
  });

  it("parses **bold**", () => {
    expect(parseInline("a **strong** word")).toEqual([
      { type: "text", value: "a " },
      { type: "bold", value: "strong" },
      { type: "text", value: " word" },
    ]);
  });

  it("parses *italic* and _italic_", () => {
    expect(parseInline("*soft*")).toEqual([{ type: "italic", value: "soft" }]);
    expect(parseInline("_soft_")).toEqual([{ type: "italic", value: "soft" }]);
  });

  it("parses `inline code`", () => {
    expect(parseInline("run `npm test` now")).toEqual([
      { type: "text", value: "run " },
      { type: "code", value: "npm test" },
      { type: "text", value: " now" },
    ]);
  });

  it("keeps markers inside code literal", () => {
    expect(parseInline("`a*b**c`")).toEqual([{ type: "code", value: "a*b**c" }]);
  });

  it("leaves an unmatched emphasis marker literal", () => {
    expect(parseInline("3 * 4 and a *b")).toEqual([{ type: "text", value: "3 * 4 and a *b" }]);
    expect(parseInline("**unclosed")).toEqual([{ type: "text", value: "**unclosed" }]);
  });

  it("leaves an unmatched backtick literal", () => {
    expect(parseInline("a `b")).toEqual([{ type: "text", value: "a `b" }]);
  });

  it("does not italicise snake_case identifiers", () => {
    expect(parseInline("call list_files_once")).toEqual([
      { type: "text", value: "call list_files_once" },
    ]);
  });
});

describe("parseMarkdown", () => {
  it("returns nothing for empty or whitespace input", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("   \n  ")).toEqual([]);
  });

  it("splits paragraphs on a blank line", () => {
    expect(parseMarkdown("first\n\nsecond")).toEqual([
      { type: "paragraph", tokens: [{ type: "text", value: "first" }] },
      { type: "paragraph", tokens: [{ type: "text", value: "second" }] },
    ]);
  });

  it("collapses a soft line break into a space", () => {
    expect(parseMarkdown("one\ntwo")).toEqual([
      { type: "paragraph", tokens: [{ type: "text", value: "one two" }] },
    ]);
  });

  it("parses ## and ### headings", () => {
    expect(parseMarkdown("## Title")).toEqual([
      { type: "heading", level: 2, tokens: [{ type: "text", value: "Title" }] },
    ]);
    expect(parseMarkdown("### Sub")).toEqual([
      { type: "heading", level: 3, tokens: [{ type: "text", value: "Sub" }] },
    ]);
  });

  it("parses - and * bullet lists", () => {
    const expected = {
      type: "bullets" as const,
      items: [
        [{ type: "text" as const, value: "one" }],
        [{ type: "text" as const, value: "two" }],
      ],
    };
    expect(parseMarkdown("- one\n- two")).toEqual([expected]);
    expect(parseMarkdown("* one\n* two")).toEqual([expected]);
  });

  it("parses 1. and 1) numbered lists", () => {
    const expected = {
      type: "ordered" as const,
      items: [
        [{ type: "text" as const, value: "one" }],
        [{ type: "text" as const, value: "two" }],
      ],
    };
    expect(parseMarkdown("1. one\n2. two")).toEqual([expected]);
    expect(parseMarkdown("1) one\n2) two")).toEqual([expected]);
  });

  it("parses inline markup inside list items", () => {
    expect(parseMarkdown("- **bold** item")).toEqual([
      {
        type: "bullets",
        items: [[{ type: "bold", value: "bold" }, { type: "text", value: " item" }]],
      },
    ]);
  });

  it("does not mistake an italic line for a bullet", () => {
    expect(parseMarkdown("*soft*")).toEqual([
      { type: "paragraph", tokens: [{ type: "italic", value: "soft" }] },
    ]);
  });

  it("separates a paragraph, a list and a trailing paragraph", () => {
    const blocks = parseMarkdown("intro:\n- one\n- two\n\noutro");
    expect(blocks.map((block) => block.type)).toEqual(["paragraph", "bullets", "paragraph"]);
  });

  it("normalises CRLF and old-Mac line endings", () => {
    expect(parseMarkdown("a\r\n\r\nb").map((block) => block.type)).toEqual([
      "paragraph",
      "paragraph",
    ]);
  });
});
