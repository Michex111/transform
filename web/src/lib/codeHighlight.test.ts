import { describe, expect, it } from "vitest";

import { normaliseLanguage, tokenizeCode } from "@/lib/codeHighlight";

/** The universal invariant: highlighting must never alter the text. */
function textOf(code: string, language: string): string {
  return tokenizeCode(code, language)
    .map((token) => token.text)
    .join("");
}

const PY = `from transform import Transform

client = Transform(api_key="tr_live_123")  # authenticate
job = client.files.convert(file_id="file_123", target_format="pdf")`;

const TS = `import { Transform } from "transform";

const client = new Transform({ apiKey: "tr_live_123" }); // authenticate
const job = await client.files.convert("file_123", "pdf");`;

const BASH = `curl -X POST https://api.example.com/jobs \\
  -H "Authorization: Bearer $TOKEN"  # one request`;

const JSON_SNIPPET = `{ "mcpServers": { "transform": { "url": "https://x/mcp" } } }`;

describe("tokenizeCode round-trip", () => {
  it.each([
    ["python", PY],
    ["typescript", TS],
    ["bash", BASH],
    ["json", JSON_SNIPPET],
    ["text", PY],
  ])("preserves the source text for %s", (language, code) => {
    expect(textOf(code, language)).toBe(code);
  });

  it("returns nothing for empty input", () => {
    expect(tokenizeCode("", "python")).toEqual([]);
  });
});

describe("token classes", () => {
  it("marks a Python line comment", () => {
    const tokens = tokenizeCode(PY, "python");
    const comment = tokens.find((t) => t.type === "comment");
    expect(comment?.text).toBe("# authenticate");
  });

  it("marks a TypeScript line comment", () => {
    const tokens = tokenizeCode(TS, "typescript");
    expect(tokens.some((t) => t.type === "comment" && t.text === "// authenticate")).toBe(true);
  });

  it("marks strings including their quotes", () => {
    const tokens = tokenizeCode(`x = "hello"`, "python");
    const string = tokens.find((t) => t.type === "string");
    expect(string?.text).toBe('"hello"');
  });

  it("marks Python keywords", () => {
    const keywords = tokenizeCode(PY, "python")
      .filter((t) => t.type === "keyword")
      .map((t) => t.text);
    expect(keywords).toContain("from");
    expect(keywords).toContain("import");
  });

  it("marks a capitalised Python constant keyword", () => {
    const tokens = tokenizeCode("x = None", "python");
    expect(tokens.find((t) => t.text === "None")?.type).toBe("keyword");
  });

  it("marks numbers but not the digits inside an identifier", () => {
    const tokens = tokenizeCode("v2 = 25", "typescript");
    const numbers = tokens.filter((t) => t.type === "number").map((t) => t.text);
    expect(numbers).toEqual(["25"]);
  });

  it("does not treat a digit as a number when it continues an identifier", () => {
    const tokens = tokenizeCode("sha256", "python");
    expect(tokens.some((t) => t.type === "number")).toBe(false);
  });

  it("treats a keyword as an ordinary identifier inside a string", () => {
    const tokens = tokenizeCode(`s = "import"`, "python");
    expect(tokens.some((t) => t.type === "keyword")).toBe(false);
  });
});

describe("normaliseLanguage", () => {
  it("folds the common spellings", () => {
    expect(normaliseLanguage("sh")).toBe("bash");
    expect(normaliseLanguage("Shell")).toBe("bash");
    expect(normaliseLanguage("ts")).toBe("typescript");
    expect(normaliseLanguage("TSX")).toBe("typescript");
    expect(normaliseLanguage("py")).toBe("python");
  });

  it("passes an unknown language through lower-cased", () => {
    expect(normaliseLanguage("Rust")).toBe("rust");
  });
});

describe("plain text", () => {
  // Prose rendered through CodeBlock (the MCP agent prompt) must not be
  // recoloured as source: an unknown language used to fall back to the
  // TypeScript keyword list, and `//` inside a URL was treated as a comment.
  it("does not colour ordinary words as keywords", () => {
    const tokens = tokenizeCode("type return const interface", "text");
    expect(tokens.every((t) => t.type === "plain")).toBe(true);
  });

  it("does not treat // inside a URL as a comment", () => {
    const line = "endpoint: https://transform-api-7b3g.onrender.com/mcp";
    const tokens = tokenizeCode(line, "text");
    expect(tokens.some((t) => t.type === "comment")).toBe(false);
    expect(tokens.map((t) => t.text).join("")).toBe(line);
  });

  it("still treats a real // as a comment in a language that has one", () => {
    // Guards against 'fix plain text' accidentally disabling comments entirely.
    const tokens = tokenizeCode("const x = 1; // note", "typescript");
    expect(tokens.some((t) => t.type === "comment")).toBe(true);
  });

  it("never alters the text, whatever the language", () => {
    const code = "https://a.example/x?y=1&z=2 // trailing";
    expect(tokenizeCode(code, "text").map((t) => t.text).join("")).toBe(code);
  });
});
