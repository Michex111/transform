// A tiny, dependency-free Markdown subset for assistant output.
//
// The assistant is instructed to write "plain text with light Markdown", and
// the transcript previously rendered those `**asterisks**` literally. This
// module turns that subset into a token tree that a React component renders as
// real elements. It deliberately does NOT produce an HTML string: model output
// must never reach the DOM through `dangerouslySetInnerHTML`, so there is no
// HTML parser here at all — only the handful of constructs below.
//
// Supported:
//   - paragraphs (blank line separated; a soft line break collapses to a space);
//   - `-` / `*` bullet lists and `1.` / `1)` numbered lists;
//   - `##` and `###` headings;
//   - inline `**bold**`, `*italic*` / `_italic_`, and `` `inline code` ``.
//
// Unmatched markers render literally — a lone `*` stays a `*` and never
// swallows the text around it — because a streaming answer is frequently
// observed mid-marker and must not lose characters.

export type InlineToken =
  | { type: "text"; value: string }
  | { type: "bold"; value: string }
  | { type: "italic"; value: string }
  | { type: "code"; value: string };

export type MarkdownBlock =
  | { type: "paragraph"; tokens: InlineToken[] }
  | { type: "heading"; level: number; tokens: InlineToken[] }
  | { type: "bullets"; items: InlineToken[][] }
  | { type: "ordered"; items: InlineToken[][] };

const BULLET = /^\s*[-*]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^(#{2,3})\s+(.+)$/;

/** Whitespace at either end of a span defeats the emphasis it wraps. */
function isFlanked(text: string): boolean {
  return text.length > 0 && !/^\s/.test(text) && !/\s$/.test(text);
}

/**
 * Index of the closing marker for an emphasis span opened at `from`, or `-1`.
 *
 * `*` closers that are half of a `**` pair are skipped so bold text is not
 * mistaken for italic, and `_` only closes on a word boundary so `snake_case`
 * identifiers stay literal.
 */
function findEmphasisClose(text: string, from: number, marker: string): number {
  for (let j = from; j < text.length; j++) {
    if (text[j] !== marker) continue;
    if (marker === "*" && (text[j - 1] === "*" || text[j + 1] === "*")) continue;
    if (!isFlanked(text.slice(from, j))) continue;
    if (marker === "_" && /[A-Za-z0-9]/.test(text[j + 1] ?? "")) continue;
    return j;
  }
  return -1;
}

/** Split one line into inline tokens, leaving unmatched markers literal. */
export function parseInline(input: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let buffer = "";
  let i = 0;

  function flush(): void {
    if (buffer) {
      tokens.push({ type: "text", value: buffer });
      buffer = "";
    }
  }

  while (i < input.length) {
    const char = input[i];

    if (char === "`") {
      const end = input.indexOf("`", i + 1);
      if (end !== -1) {
        flush();
        tokens.push({ type: "code", value: input.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
      buffer += char;
      i += 1;
      continue;
    }

    if (char === "*" && input[i + 1] === "*") {
      const end = input.indexOf("**", i + 2);
      if (end !== -1 && isFlanked(input.slice(i + 2, end))) {
        flush();
        tokens.push({ type: "bold", value: input.slice(i + 2, end) });
        i = end + 2;
        continue;
      }
      buffer += char;
      i += 1;
      continue;
    }

    if (char === "*" || char === "_") {
      // An `_` glued to a word is part of the word (`snake_case`), not emphasis.
      if (char === "_" && /[A-Za-z0-9]/.test(input[i - 1] ?? "")) {
        buffer += char;
        i += 1;
        continue;
      }
      const end = findEmphasisClose(input, i + 1, char);
      if (end !== -1) {
        flush();
        tokens.push({ type: "italic", value: input.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
      buffer += char;
      i += 1;
      continue;
    }

    buffer += char;
    i += 1;
  }

  flush();
  return tokens;
}

/**
 * Parse assistant text into blocks.
 *
 * Non-string input and whitespace-only input yield `[]`, so a caller can render
 * nothing rather than an empty paragraph.
 */
export function parseMarkdown(input: string): MarkdownBlock[] {
  if (typeof input !== "string" || input.trim().length === 0) return [];

  const lines = input.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  function flushParagraph(): void {
    if (paragraph.length === 0) return;
    // A soft line break is a space — the same collapse a Markdown renderer does.
    blocks.push({ type: "paragraph", tokens: parseInline(paragraph.join(" ")) });
    paragraph = [];
  }

  function flushList(): void {
    if (!list) return;
    blocks.push(
      list.ordered
        ? { type: "ordered", items: list.items.map(parseInline) }
        : { type: "bullets", items: list.items.map(parseInline) },
    );
    list = null;
  }

  function flush(): void {
    flushParagraph();
    flushList();
  }

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flush();
      continue;
    }

    const heading = HEADING.exec(line.trim());
    if (heading) {
      flush();
      blocks.push({
        type: "heading",
        level: heading[1].length,
        tokens: parseInline(heading[2].trim()),
      });
      continue;
    }

    const bullet = BULLET.exec(line);
    if (bullet) {
      flushParagraph();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1].trim());
      continue;
    }

    const ordered = ORDERED.exec(line);
    if (ordered) {
      flushParagraph();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(ordered[1].trim());
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  flush();
  return blocks;
}
