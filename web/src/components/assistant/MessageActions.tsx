import { useEffect, useRef, useState } from "react";
import { ArrowsClockwise, Check, Copy } from "@phosphor-icons/react";
import { copyText } from "@/lib/clipboard";

/**
 * Copy + Retry controls under an assistant answer.
 *
 * Two visibility rules, and both matter:
 *
 *   - the ROW is revealed on hover for a fine pointer, but never hidden from a
 *     keyboard: the buttons stay in the DOM and focusable, and focusing either
 *     one (`group-focus-within` on the message) brings the row back. On a coarse
 *     pointer it is always visible, because there is no hover to reveal it with.
 *   - each LABEL is icon-only at rest on a fine pointer and expands on the
 *     button's own hover/focus. On a coarse pointer the label stays visible —
 *     there is no hover to reveal it — and the 44px touch target is preserved.
 *
 * The "Copied" confirmation is never hidden behind a hover: once Copy has been
 * pressed the label is forced open, because feedback the user has to hover to
 * find is not feedback. The accessible name lives on `aria-label`, so it never
 * depends on a label that is visually collapsed (or absent) at rest.
 *
 * Only rendered for a finished answer — a streaming or pending turn has nothing
 * stable to copy and nothing to regenerate yet.
 */
export function MessageActions({
  content,
  onRetry,
}: {
  content: string;
  onRetry?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  async function handleCopy() {
    const ok = await copyText(content);
    if (!ok) return;
    setCopied(true);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="flex items-center gap-1 opacity-100 transition-opacity pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100">
      <button
        type="button"
        onClick={() => void handleCopy()}
        aria-label="Copy answer"
        className="group/action inline-flex items-center rounded-md p-1.5 text-xs font-medium text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:px-2.5"
      >
        {copied ? (
          <Check size={14} weight="bold" className="shrink-0 text-success" />
        ) : (
          <Copy size={14} className="shrink-0" />
        )}
        <ActionLabel label={copied ? "Copied" : "Copy"} force={copied} live />
      </button>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          aria-label="Regenerate answer"
          className="group/action inline-flex items-center rounded-md p-1.5 text-xs font-medium text-muted transition-colors hover:bg-surface-variant hover:text-on-background pointer-coarse:min-h-11 pointer-coarse:px-2.5"
        >
          <ArrowsClockwise size={14} className="shrink-0" />
          <ActionLabel label="Retry" force={false} />
        </button>
      )}
    </div>
  );
}

/**
 * A button's visible label.
 *
 * At rest on a fine pointer it is collapsed to nothing (`max-w-0`, with no
 * padding of its own — the padding lives on the inner span so a zero-width box
 * really is zero-width) and expands on the button's hover or focus within it. A
 * coarse pointer can never hover, so there it is expanded from the start. Every
 * expanded state uses the *same* values, so no two utilities ever compete for
 * the same property and the result cannot depend on stylesheet order.
 */
function ActionLabel({
  label,
  force,
  live = false,
}: {
  label: string;
  /** Keep it open regardless of hover — used for the "Copied" confirmation. */
  force: boolean;
  /** Announce changes (the copy confirmation) via `aria-live`. */
  live?: boolean;
}) {
  return (
    <span
      aria-live={live ? "polite" : undefined}
      className={`overflow-hidden whitespace-nowrap transition-[max-width,opacity] duration-200 ${
        force
          ? "max-w-24 opacity-100"
          : "max-w-0 opacity-0 group-hover/action:max-w-24 group-hover/action:opacity-100 group-focus-within/action:max-w-24 group-focus-within/action:opacity-100 pointer-coarse:max-w-24 pointer-coarse:opacity-100"
      }`}
    >
      <span className="pl-1.5">{label}</span>
    </span>
  );
}
