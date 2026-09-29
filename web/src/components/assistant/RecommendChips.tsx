import { Check } from "@phosphor-icons/react";
import { FormatThumb } from "@/components/FormatThumb";
import { Skeleton } from "@/components/ui";
import type { RecommendState } from "@/lib/useAssistantRecommend";

/**
 * The assistant's suggested target formats, as selectable rows.
 *
 * Shared by the library file dialog and the Convert page, which differ only in
 * how they ask for the suggestion. A suggestion the conversion graph cannot
 * actually produce is shown disabled with the reason why, rather than hidden or
 * silently clickable — the answer the model gave is still information, and a
 * button that appeared to work but did nothing would be worse than a disabled
 * one that explains itself.
 */
export function RecommendChips({
  state,
  availableTargets,
  selected,
  sourceFormat,
  onSelect,
}: {
  state: RecommendState;
  availableTargets: string[];
  selected: string;
  sourceFormat: string;
  onSelect: (target: string) => void;
}) {
  if (state.status === "loading") {
    return (
      <div className="space-y-2" role="status" aria-label="Finding formats to suggest">
        <Skeleton className="h-14 w-full rounded-lg" />
        <Skeleton className="h-14 w-full rounded-lg" />
      </div>
    );
  }

  if (state.status === "failed") {
    return (
      <p className="text-xs text-muted">
        AI suggestions aren&apos;t available right now — pick a format below.
      </p>
    );
  }

  if (state.status !== "ready" || state.recommendations.length === 0) return null;

  const available = new Set(availableTargets.map((target) => target.toLowerCase()));
  const source = sourceFormat.toUpperCase();

  return (
    <div className="space-y-2 rounded-xl border border-primary/30 bg-primary-container/15 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">Suggested with AI</p>
      <ul className="space-y-1.5">
        {state.recommendations.map((rec) => {
          const selectable = available.has(rec.target_format.toLowerCase());
          const isSelected = rec.target_format.toLowerCase() === selected.toLowerCase();
          return (
            <li key={rec.target_format}>
              <button
                type="button"
                disabled={!selectable}
                onClick={() => onSelect(rec.target_format)}
                title={rec.reason}
                className={`flex w-full items-center gap-2 rounded-lg border px-2 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  isSelected
                    ? "border-primary bg-primary-container"
                    : "border-outline bg-surface hover:border-primary/50"
                }`}
              >
                <FormatThumb format={rec.target_format} size="sm" label="" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-on-background">
                    {rec.label || rec.target_format.toUpperCase()}
                  </span>
                  <span className="block truncate text-xs text-muted">
                    {selectable ? rec.reason : `Not available for ${source}`}
                  </span>
                </span>
                {rec.confidence > 0 && (
                  <span className="shrink-0 font-mono text-[11px] text-muted">
                    {Math.round(rec.confidence * 100)}%
                  </span>
                )}
                {isSelected && <Check size={14} weight="bold" className="shrink-0 text-primary" />}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
