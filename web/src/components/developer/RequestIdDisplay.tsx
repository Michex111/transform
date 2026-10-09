import { Check, Copy } from "@phosphor-icons/react";
import { useState } from "react";

/**
 * A copyable identifier (a request id, a connection id).
 *
 * Monospace, truncated with the full value in `title`, and a copy button that
 * reports success through `aria-live` — a copy affordance with no feedback is
 * indistinguishable from a broken one, and request ids are the thing a user
 * pastes into a support thread.
 */
export function RequestIdDisplay({ id, label = "Request ID" }: { id: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be denied (an insecure context, a permission
      // prompt). The id is still selectable, so this is not worth an error
      // toast — the user can select it manually.
      setCopied(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <code
        title={id}
        className="min-w-0 truncate rounded bg-surface-sunken px-2 py-1 font-mono text-xs text-on-background"
      >
        {id}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={`Copy ${label.toLowerCase()}`}
        className="shrink-0 rounded-md border border-outline p-1.5 text-muted transition-colors hover:bg-surface-variant hover:text-on-background focus-visible:ring-2 focus-visible:ring-primary"
      >
        {copied ? <Check size={14} className="text-success" aria-hidden /> : <Copy size={14} aria-hidden />}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {copied ? `${label} copied to clipboard` : ""}
      </span>
    </div>
  );
}
