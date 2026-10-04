/**
 * The plan and upgrade decisions on the Billing page.
 *
 * The page has to answer three questions that are easy to get subtly wrong and
 * impossible to exercise in this repo's Node test environment (no jsdom): what
 * does the current plan cost and when does it renew, which plan is offered as
 * the next step up, and what concrete things does that plan include. All three
 * are answered from the API's own payload — a number that is not in the payload
 * is omitted, never defaulted to zero.
 *
 * The component keeps only the "call the API and render the result" half.
 */

import { canChangePlanTo, tierRank } from "@/lib/planChange";
import { formatUsd } from "@/lib/creditPacks";
import { formatDateOrNull } from "@/lib/format";
import type { SubscriptionPlanResponse, SubscriptionStatusResponse } from "@/api/types";

/** The plan record for a tier, or `null` when the API did not list it. */
export function planForTier(
  plans: readonly SubscriptionPlanResponse[],
  tier: string | null | undefined,
): SubscriptionPlanResponse | null {
  const wanted = (tier ?? "").trim().toUpperCase();
  if (!wanted) return null;
  return plans.find((p) => p.tier.trim().toUpperCase() === wanted) ?? null;
}

/**
 * The current plan's monthly price as copy, or `null` when the API did not
 * quote one.
 *
 * A Free plan, an Enterprise "contact us" plan and an older API all leave
 * `price_monthly_usd` null, and the honest rendering is to leave the price off
 * the hero entirely rather than print "$0".
 */
export function monthlyPriceLabel(
  plans: readonly SubscriptionPlanResponse[],
  tier: string | null | undefined,
): string | null {
  const price = planForTier(plans, tier)?.price_monthly_usd;
  if (typeof price !== "number" || !Number.isFinite(price) || price <= 0) return null;
  return `${formatUsd(price)}/month`;
}

export type PlanTone = "success" | "warning" | "error" | "muted";

/**
 * The tone of the plan-status badge for a Stripe subscription status.
 *
 * Deliberately narrow: an unrecognised status (a Stripe value added later) is
 * "muted", not "success", so a page never colour-codes an unknown state as
 * healthy. The raw status is still shown as the label.
 */
export function planStatusTone(status: string | null | undefined): PlanTone {
  switch ((status ?? "").trim().toLowerCase()) {
    case "active":
    case "trialing":
      return "success";
    case "past_due":
    case "paused":
    case "incomplete":
      return "warning";
    case "canceled":
    case "cancelled":
    case "unpaid":
    case "incomplete_expired":
      return "error";
    default:
      return "muted";
  }
}

/**
 * The period line under the plan name.
 *
 * Reads "Renews <date>" normally, but "Ends <date>" once the subscription is
 * scheduled to stop — a "Renews" label next to a cancellation notice would
 * contradict the notice. `cancel_at_period_end` is tri-state (an older API does
 * not send it), and only an explicit `true` changes the wording.
 */
export function periodLabel(
  status: Pick<
    SubscriptionStatusResponse,
    "current_period_end" | "cancel_at_period_end"
  > | null | undefined,
): string | null {
  const date = formatDateOrNull(status?.current_period_end);
  if (!date) return null;
  return status?.cancel_at_period_end === true ? `Ends ${date}` : `Renews ${date}`;
}

export interface PlanUpgrade {
  plan: SubscriptionPlanResponse;
  /**
   * Whether `changePlan` can reach it from here. False for a Free account
   * (which must start a subscription through checkout) and for Enterprise
   * (which is not self-serve) — the page routes those to `/pricing` and
   * `/app/support` respectively instead of rendering a dead button.
   */
  selfServe: boolean;
}

/**
 * The plan immediately above `tier` in the plan order, or `null` at the top.
 *
 * Ranked with the same table as `planChange`, so the legacy `PREMIUM` alias
 * cannot be treated as a step below `PRO`. Only `plans.filter(...)` results are
 * considered, so an API that lists no higher plan yields `null` and the nudge
 * card is omitted rather than invented.
 */
export function nextPlanAbove(
  plans: readonly SubscriptionPlanResponse[],
  tier: string | null | undefined,
): PlanUpgrade | null {
  const rank = tierRank(tier);
  if (rank >= 3) return null;
  const plan = plans
    .filter((p) => tierRank(p.tier) > rank)
    .sort((a, b) => tierRank(a.tier) - tierRank(b.tier))[0];
  if (!plan) return null;
  return { plan, selfServe: canChangePlanTo(tier, plan.tier) };
}

/**
 * Up to three concrete inclusions of `plan`, straight from its payload.
 *
 * Every line is a field the API actually sent: there is no fallback text and no
 * hardcoded plan size, so an older API that omits the structured fields yields
 * a shorter list (or none) rather than a plausible-looking invention. Ordered
 * by what a person notices first.
 */
export function upgradeGains(
  plan: SubscriptionPlanResponse | null | undefined,
): string[] {
  if (!plan) return [];
  const gains: string[] = [];

  if (typeof plan.monthly_credits === "number" && plan.monthly_credits > 0) {
    gains.push(`${plan.monthly_credits} conversions a month`);
  }
  if (typeof plan.storage_gb === "number" && plan.storage_gb > 0) {
    gains.push(`${plan.storage_gb} GB of storage`);
  }
  const model = plan.ai?.model_label?.trim();
  if (model) gains.push(`${model} AI assistant`);
  if (plan.priority_processing === true) gains.push("Priority processing");
  if (typeof plan.api_calls_month === "number" && plan.api_calls_month > 0) {
    gains.push(`${plan.api_calls_month} API calls a month`);
  }

  return gains.slice(0, 3);
}
