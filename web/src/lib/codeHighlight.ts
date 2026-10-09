/**
 * A tiny, dependency-free code tokeniser for the developer pages.
 *
 * The repo already avoids heavy dependencies (no syntax-highlighting package),
 * and a full highlighter would add tens of kilobytes for six short snippets.
 * This recognises the handful of token classes that make a sample readable —
 * comments, strings, numbers, and keywords — and leaves everything else plain.
 *
 * Deliberately NOT a parser: it never has to be correct about semantics, only
 * to colour the obvious tokens without ever dropping or reordering a character.
 * The invariant `tokens.map(t => t.text).join("") === code` is what the test
 * pins, so a syntax-highlighting failure can only ever look wrong, never lose
 * the sample's text.
 */

export type CodeTokenType = "plain" | "comment" | "string" | "number" | "keyword";

export interface CodeToken {
  text: string;
  type: CodeTokenType;
}

const KEYWORDS: Record<string, string[]> = {
  python: [
    "from", "import", "def", "return", "class", "with", "as", "for", "in", "if",
    "else", "elif", "not", "None", "True", "False", "await", "async", "lambda", "print",
  ],
  typescript: [
    "import", "from", "export", "const", "let", "var", "function", "return", "await",
    "async", "interface", "type", "new", "if", "else", "class", "extends", "default",
    "void", "number", "string", "boolean", "true", "false", "null",
  ],
  javascript: [
    "import", "from", "export", "const", "let", "var", "function", "return", "await",
    "async", "new", "if", "else", "class", "extends", "default", "true", "false", "null",
  ],
  json: [],
  bash: [],
  shell: [],
  // Plain text: no keywords and (below) no comment marker. Without an entry
  // here an unknown language falls back to the TypeScript keyword list, which
  // recolours ordinary prose, and the `//` inside any URL would be treated as a
  // line comment — so a block of instructions rendered as source code.
  text: [],
  plain: [],
};

/** Normalise the many spellings of a language into a key in `KEYWORDS`. */
export function normaliseLanguage(language: string): string {
  const lang = language.trim().toLowerCase();
  if (lang === "sh" || lang === "shell" || lang === "console") return "bash";
  if (lang === "js") return "javascript";
  if (lang === "ts" || lang === "tsx") return "typescript";
  if (lang === "py") return "python";
  return lang;
}

function lineCommentMarker(language: string): string | null {
  const lang = normaliseLanguage(language);
  if (lang === "python" || lang === "bash") return "#";
  // Prose carries `//` inside URLs (`https://…`), so treating it as a comment
  // marker would grey out the rest of every link-bearing line.
  if (lang === "json" || lang === "text" || lang === "plain") return null;
  return "//";
}

const IDENT_START = /[A-Za-z_$]/;
const IDENT_PART = /[A-Za-z0-9_$]/;
const NUMBER_PART = /[0-9._]/;

/** Split source into coloured tokens without ever altering the text. */
export function tokenizeCode(code: string, language: string): CodeToken[] {
  const lang = normaliseLanguage(language);
  const comment = lineCommentMarker(lang);
  const keywords = new Set(KEYWORDS[lang] ?? KEYWORDS.typescript);

  const tokens: CodeToken[] = [];
  let plain = "";
  const flush = () => {
    if (plain) {
      tokens.push({ text: plain, type: "plain" });
      plain = "";
    }
  };

  let i = 0;
  while (i < code.length) {
    const ch = code[i];

    // String literal — consume to the matching unescaped quote.
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      let j = i + 1;
      while (j < code.length && code[j] !== quote) {
        if (code[j] === "\\") j++;
        j++;
      }
      j = Math.min(j + 1, code.length);
      flush();
      tokens.push({ text: code.slice(i, j), type: "string" });
      i = j;
      continue;
    }

    // Line comment — consume to end of line (newline left for the plain run).
    if (comment && code.startsWith(comment, i)) {
      let end = code.indexOf("\n", i);
      if (end === -1) end = code.length;
      flush();
      tokens.push({ text: code.slice(i, end), type: "comment" });
      i = end;
      continue;
    }

    // Number — only when it starts a token (not the tail of an identifier).
    if (/[0-9]/.test(ch) && !IDENT_PART.test(code[i - 1] ?? "")) {
      let j = i;
      while (j < code.length && NUMBER_PART.test(code[j])) j++;
      flush();
      tokens.push({ text: code.slice(i, j), type: "number" });
      i = j;
      continue;
    }

    // Identifier — a keyword or plain text.
    if (IDENT_START.test(ch)) {
      let j = i;
      while (j < code.length && IDENT_PART.test(code[j])) j++;
      const word = code.slice(i, j);
      flush();
      tokens.push({ text: word, type: keywords.has(word) ? "keyword" : "plain" });
      i = j;
      continue;
    }

    plain += ch;
    i++;
  }

  flush();
  return tokens;
}
