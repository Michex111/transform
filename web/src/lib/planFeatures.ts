// The sentences a pricing card shows for a plan's Transform AI entitlements.
//
// Kept out of the page so the pluralisation and formatting are pinned by tests
// instead of done with inline string surgery in JSX. Pure: no DOM, no React, so
// it runs in the Node test environment.

import type { AiEntitlement } from "@/api/types";

/** A usable positive count, or `null` for a missing/zero/negative value. */
function positive(value: number): number | null {
  return Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * The rows for the "AI assistant" group of one plan card.
 *
 * Returns `[]` — never a "None" row — when the plan has no assistant or the
 * entitlement payload was unusable, so the caller renders no group at all and
 * the card looks exactly as it did before the AI contract existed.
 *
 * A missing or non-positive number is omitted rather than printed: a plan that
 * includes the assistant has at least one request an hour and one attachment,
 * so a `0` only ever means "the API didn't say", and "0 requests per hour" would
 * be a false claim about the plan.
 */
export function aiPlanFeatures(ai: AiEntitlement | null | undefined): string[] {
  if (!ai) return [];

  const rows: string[] = [];

  const label = ai.model_label.trim();
  if (label) rows.push(`${label} AI model`);

  const requests = positive(ai.requests_per_hour);
  if (requests !== null) {
    rows.push(`${requests} ${requests === 1 ? "request" : "requests"} per hour`);
  }

  const attachments = positive(ai.max_attachments);
  if (attachments !== null) {
    rows.push(
      `${attachments} ${attachments === 1 ? "attachment" : "attachments"} per message`,
    );
  }

  const documentMb = positive(ai.max_document_mb);
  if (documentMb !== null) {
    rows.push(`Up to ${documentMb} MB per document`);
  }

  return rows;
}
