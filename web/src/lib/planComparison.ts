/**
 * The rows of the public Pricing page's feature comparison table.
 *
 * Every cell is derived from the plan payload — never from the prose
 * `features` strings, which cannot be parsed honestly. A row whose values are
 * all `null` is dropped, because "the API did not send this" must not render as
 * a claim (not even a blank one). Pure: no DOM, no React, so it runs in the
 * Node test environment and can be unit-tested row by row.
 */

import type { SubscriptionPlanResponse } from "@/api/types";
import { formatCount } from "@/lib/pricingPlans";

/** One comparison row; `values` is aligned index-for-index with the plans. */
export interface ComparisonRow {
  label: string;
  values: (string | boolean | null)[];
}

export interface ComparisonSection {
  title: string;
  rows: ComparisonRow[];
}

/** A usable positive count, or `null` for a missing/zero/negative value. */
function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** Trimmed text, or `null` when the API sent nothing usable. */
function text(value: string | null | undefined): string | null {
  const trimmed = (value ?? "").trim();
  return trimmed || null;
}

/** A grouped count, or `null` when unstated. */
function count(value: number | null | undefined): string | null {
  const n = positive(value);
  return n === null ? null : formatCount(n);
}

/** A size in megabytes, or `null` when unstated. */
function megabytes(value: number | null | undefined): string | null {
  const n = positive(value);
  return n === null ? null : `${formatCount(n)} MB`;
}

/** A row is worth rendering only when at least one plan stated a value. */
function isStated(row: ComparisonRow): boolean {
  return row.values.some((value) => value !== null);
}

/** Build a section, dropping rows (and the section itself) with nothing to say. */
function section(title: string, rows: ComparisonRow[]): ComparisonSection | null {
  const kept = rows.filter(isStated);
  return kept.length > 0 ? { title, rows: kept } : null;
}

/**
 * The comparison table's sections, in reading order.
 *
 * Values come from the structured fields (`storage_gb`, `monthly_credits`,
 * `api_calls_month`, `priority_processing`, `support_level`, `ai.*`). Additive
 * fields that an older API omits leave their row `null`, and a row that is
 * `null` for every plan is dropped — so the table degrades to exactly the rows
 * the payload can support rather than inventing limits.
 */
export function comparisonSections(plans: SubscriptionPlanResponse[]): ComparisonSection[] {
  if (plans.length === 0) return [];

  const sections = [
    section("Conversions & limits", [
      {
        label: "Monthly conversions",
        // `null` credits is the API's "unlimited" (no integer for uncapped).
        values: plans.map((plan) =>
          plan.monthly_credits == null ? "Unlimited" : formatCount(plan.monthly_credits),
        ),
      },
      {
        label: "Priority processing",
        values: plans.map((plan) => plan.priority_processing ?? null),
      },
    ]),
    section("Storage", [
      {
        label: "Storage",
        values: plans.map((plan) => `${formatCount(plan.storage_gb)} GB`),
      },
    ]),
    section("Developer API", [
      {
        label: "API calls per month",
        values: plans.map((plan) => {
          const stated = count(plan.api_calls_month);
          if (stated !== null) return stated;
          // An uncapped plan reports no integer for either cap, so "no
          // conversions limit AND no API-call figure" is the API's own way of
          // saying the plan is not metered. An older API that simply does not
          // send `api_calls_month` still has a real `monthly_credits`, so this
          // can never turn a missing field into a false claim of "Unlimited".
          return plan.monthly_credits == null ? "Unlimited" : null;
        }),
      },
    ]),
    section("AI assistant", [
      {
        label: "AI model",
        values: plans.map((plan) => text(plan.ai?.model_label)),
      },
      {
        label: "AI requests per hour",
        values: plans.map((plan) => count(plan.ai?.requests_per_hour)),
      },
      {
        label: "Attachments per message",
        values: plans.map((plan) => count(plan.ai?.max_attachments)),
      },
      {
        label: "Max document size",
        values: plans.map((plan) => megabytes(plan.ai?.max_document_mb)),
      },
      {
        label: "AI actions per turn",
        values: plans.map((plan) => count(plan.ai?.max_actions_per_turn)),
      },
    ]),
    section("Support", [
      {
        label: "Support level",
        values: plans.map((plan) => text(plan.support_level)),
      },
    ]),
  ];

  return sections.filter((entry): entry is ComparisonSection => entry !== null);
}
