import { useId, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import type { AssistantStep } from "@/lib/assistantChat";

/**
 * The steps a reopened assistant turn took, folded into its bubble.
 *
 * Deliberately quiet — a small muted disclosure, not a second chat bubble: it
 * is the editor-style "here is what I did" line, collapsed by default so the
 * answer stays the focus and the steps are available on demand. The trigger is
 * a real `<button aria-expanded>` and the list is always in the DOM (hidden
 * while collapsed) so `aria-controls` never dangles.
 */
export function ToolSteps({ steps }: { steps: AssistantStep[] }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  if (steps.length === 0) return null;

  const count = steps.length;
  const noun = count === 1 ? "step" : "steps";
  // "Activity", not "steps": the panel answers "what did it actually do?", and
  // the count is carried in the accessible name so the visible label can stay
  // short without hiding the number from a screen reader.
  const label = open ? "Hide activity" : "View activity";

  return (
    <div className="space-y-1.5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${label}, ${count} ${noun}`}
        className="inline-flex items-center gap-1.5 rounded-md px-1 py-0.5 text-xs text-muted transition-colors hover:text-on-background pointer-coarse:min-h-11"
      >
        <CaretDown
          size={12}
          weight="bold"
          aria-hidden
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
        <span>{label}</span>
        <span className="text-muted/70" aria-hidden>
          {count}
        </span>
      </button>

      <ol id={panelId} hidden={!open} className="space-y-1.5 border-l border-outline pl-3">
        {steps.map((step, index) => (
          <li key={`${step.name}:${index}`} className="text-xs">
            <span className="text-on-background/80">{step.label}</span>
            {step.summary ? <span className="block text-muted">{step.summary}</span> : null}
          </li>
        ))}
      </ol>
    </div>
  );
}
