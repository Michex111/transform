import { useState } from "react";
import { Check, Copy } from "@phosphor-icons/react";
import { copyText } from "@/lib/clipboard";
import { tokenizeCode, type CodeTokenType } from "@/lib/codeHighlight";

const TOKEN_CLASS: Record<CodeTokenType, string> = {
  plain: "text-on-surface",
  comment: "text-muted italic",
  string: "text-emerald-300",
  number: "text-amber-300",
  keyword: "text-sky-300",
};

/**
 * A read-only code sample with a copy control.
 *
 * Used by the developer and MCP pages. The highlighting is the repo's own tiny
 * tokeniser (`lib/codeHighlight.ts`) rather than a syntax-highlighting package,
 * so a snippet costs no extra dependency. The copy button reuses the shared
 * `copyText` helper, which falls back to `execCommand` on insecure origins.
 */
export function CodeBlock({
  code,
  language = "text",
  label,
}: {
  code: string;
  language?: string;
  /** A short caption shown in the header (e.g. "Python", "cURL"). */
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const tokens = tokenizeCode(code, language);

  async function handleCopy() {
    const ok = await copyText(code);
    if (!ok) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <figure className="min-w-0 max-w-full overflow-hidden rounded-lg border border-outline bg-surface-sunken shadow-e1">
      <figcaption className="flex items-center justify-between gap-2 border-b border-outline px-3 py-2">
        <span className="font-mono text-xs uppercase tracking-wider text-muted">
          {label ?? language}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
          aria-label={copied ? "Copied" : "Copy code"}
        >
          {copied ? <Check size={14} aria-hidden /> : <Copy size={14} aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </figcaption>
      <pre className="overflow-x-auto p-4 text-left">
        <code className="font-mono text-[13px] leading-relaxed">
          {tokens.map((token, index) => (
            <span key={index} className={TOKEN_CLASS[token.type]}>
              {token.text}
            </span>
          ))}
        </code>
      </pre>
      {/* Announce the copy result to assistive tech without a toast. */}
      <span role="status" aria-live="polite" className="sr-only">
        {copied ? "Code copied to clipboard" : ""}
      </span>
    </figure>
  );
}
