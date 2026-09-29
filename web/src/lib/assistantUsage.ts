// The caller's plan tier and hourly usage, as shown in the assistant header.
//
// Pure so the "which parts do we actually know?" logic is pinned by tests rather
// than branching inline in JSX. `AssistantStatus` arrives from an API that may
// be older than the SPA, so any of the entitlement fields can be absent.

import type { AssistantStatus } from "@/api/types";

/** The pieces of the usage sentence that are actually known. */
function knownFacts(status: AssistantStatus | null | undefined): {
  model: string;
  used: number | null;
  limit: number | null;
} {
  if (!status) return { model: "", used: null, limit: null };

  const model = status.model_label?.trim() ?? "";
  const used = status.used_this_hour;
  const limit = status.requests_per_hour;
  // Both numbers are needed to say "3/60". A limit of 0 is not a real plan
  // limit, so it is treated the same as an absent one — never "0/0 this hour".
  const usable =
    typeof used === "number" &&
    Number.isFinite(used) &&
    used >= 0 &&
    typeof limit === "number" &&
    Number.isFinite(limit) &&
    limit > 0;

  return { model, used: usable ? used : null, limit: usable ? limit : null };
}

/**
 * The compact header label, e.g. `"Advanced · 3/60 this hour"`.
 *
 * Degrades honestly rather than inventing data:
 *   - model tier + usage → `"Advanced · 3/60 this hour"`;
 *   - model tier only    → `"Advanced"`;
 *   - usage only         → `"3/60 this hour"`;
 *   - neither            → `null` (the header renders nothing).
 */
export function usageLabel(status: AssistantStatus | null | undefined): string | null {
  const { model, used, limit } = knownFacts(status);

  if (model && used !== null && limit !== null) return `${model} · ${used}/${limit} this hour`;
  if (model) return model;
  if (used !== null && limit !== null) return `${used}/${limit} this hour`;
  return null;
}

/**
 * The expanded sentence for the label's `title`/`aria-label`, e.g.
 * `"Advanced AI model · 3 of 60 AI requests used this hour"`.
 *
 * `null` whenever `usageLabel` is, so a caller can use one check for both.
 */
export function usageTitle(status: AssistantStatus | null | undefined): string | null {
  const { model, used, limit } = knownFacts(status);

  const parts: string[] = [];
  if (model) parts.push(`${model} AI model`);
  if (used !== null && limit !== null) {
    parts.push(`${used} of ${limit} AI requests used this hour`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}
