import { Sparkle } from "@phosphor-icons/react";
import { pickSuggestions } from "@/lib/suggestedPrompts";

/**
 * Example questions for the empty state.
 *
 * The pool lives in `lib/suggestedPrompts.ts` (each entry maps to a real
 * capability) and is sampled with a seed so a session's set is stable but two
 * sessions differ; `AssistantPage` reseeds on every new chat. Four is the
 * visual sweet spot in the two-column grid.
 */
export function SuggestedPrompts({
  onPick,
  seed,
}: {
  onPick: (prompt: string) => void;
  seed: string | number;
}) {
  const prompts = pickSuggestions(seed);

  return (
    <ul className="grid gap-2 sm:grid-cols-2">
      {prompts.map((prompt) => (
        <li key={prompt}>
          <button
            type="button"
            onClick={() => onPick(prompt)}
            className="flex w-full items-center gap-2 rounded-lg border border-outline bg-surface-variant/40 px-3 py-2.5 text-left text-sm text-on-background transition-colors hover:border-primary/60 hover:bg-surface-variant pointer-coarse:min-h-11"
          >
            <Sparkle size={15} weight="fill" className="shrink-0 text-primary" aria-hidden />
            <span>{prompt}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
