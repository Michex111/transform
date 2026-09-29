import { motion, useReducedMotion } from "motion/react";
import { parseMarkdown, type InlineToken, type MarkdownBlock } from "@/lib/assistantMarkdown";

/**
 * Render the assistant's light-Markdown text as real elements.
 *
 * It consumes the token tree from `parseMarkdown` and never builds an HTML
 * string — there is no `dangerouslySetInnerHTML` anywhere in this path, so model
 * output cannot become markup. Blocks are rendered as siblings (a fragment, not
 * a wrapper) so the bubble's own `space-y-*` controls the rhythm between them,
 * exactly as it did for the old paragraph list.
 */
export function RichText({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const blocks = parseMarkdown(text);

  if (blocks.length === 0) {
    // A brand-new pending turn has no text yet; show the caret on its own so the
    // bubble still signals that something is being written into it.
    return streaming ? (
      <p className="break-words">
        <StreamingCaret />
      </p>
    ) : null;
  }

  return (
    <>
      {blocks.map((block, index) => (
        <Block
          key={index}
          block={block}
          caret={streaming && index === blocks.length - 1}
        />
      ))}
    </>
  );
}

function Block({ block, caret }: { block: MarkdownBlock; caret: boolean }) {
  switch (block.type) {
    case "heading": {
      const Tag = block.level === 2 ? "h2" : "h3";
      return (
        <Tag className="font-display text-[13px] font-semibold leading-snug text-on-background">
          <Inline tokens={block.tokens} />
          {caret && <StreamingCaret />}
        </Tag>
      );
    }

    case "bullets":
    case "ordered": {
      const Tag = block.type === "bullets" ? "ul" : "ol";
      return (
        <Tag className={`${block.type === "bullets" ? "list-disc" : "list-decimal"} space-y-1 pl-5`}>
          {block.items.map((item, index) => (
            // List items are positional and never reordered, so the index is the
            // stable key (the array is a pure projection of the source text).
            <li key={index} className="break-words">
              <Inline tokens={item} />
              {caret && index === block.items.length - 1 && <StreamingCaret />}
            </li>
          ))}
        </Tag>
      );
    }

    default:
      return (
        <p className="break-words">
          <Inline tokens={block.tokens} />
          {caret && <StreamingCaret />}
        </p>
      );
  }
}

/** A small, self-describing inline run — no HTML, only real elements. */
function Inline({ tokens }: { tokens: InlineToken[] }) {
  return (
    <>
      {tokens.map((token, index) => {
        switch (token.type) {
          case "bold":
            return (
              <strong key={index} className="font-semibold">
                {token.value}
              </strong>
            );
          case "italic":
            return <em key={index}>{token.value}</em>;
          case "code":
            return (
              <code
                key={index}
                className="rounded bg-surface px-1 py-0.5 font-mono text-[0.85em] text-on-background"
              >
                {token.value}
              </code>
            );
          default:
            return token.value;
        }
      })}
    </>
  );
}

/** A quiet blinking block at the end of the text still being written. */
function StreamingCaret() {
  const reduce = useReducedMotion();
  return (
    <motion.span
      aria-hidden
      className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 rounded-sm bg-current opacity-60"
      animate={reduce ? undefined : { opacity: [0.15, 0.7, 0.15] }}
      transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}
