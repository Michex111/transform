/**
 * Rules for the public Pricing page's plan cards and credit packs.
 *
 * Deliberately pure and free of React: this repo's test environment is Node
 * (no jsdom), so anything that decides *behaviour* — which call to action a
 * plan card offers, how a plan's headline stats read, which credit pack is the
 * best value — has to live outside a component to be tested. The page keeps
 * only the requests and the rendering.
 */

import type { CreditPricingResponse, SubscriptionPlanResponse } from "@/api/types";
import { tierLabel, tierRank } from "@/lib/planChange";

/** One reusable number formatter; `Intl.NumberFormat` is expensive to build. */
const COUNT_FORMAT = new Intl.NumberFormat("en-US");

/** Group thousands so `2000` reads as `2,000`. */
export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
}

/* ------------------------------------------------------------------ */
/* Plan card call to action                                            */
/* ------------------------------------------------------------------ */

export type PlanCtaKind =
  | "register"
  | "checkout"
  | "change-plan"
  | "current"
  | "contact";

export interface PlanCta {
  kind: PlanCtaKind;
  label: string;
  disabled: boolean;
}

export interface PlanCtaInput {
  plan: SubscriptionPlanResponse;
  /** The viewer's current tier, or `null` when unknown / not signed in. */
  currentTier: string | null;
  isAuthenticated: boolean;
}

/**
 * What one plan card's button should say and do.
 *
 * Precedence (the caller renders the matching action):
 *   1. Enterprise is never self-serve — always "Contact sales".
 *   2. A plan with no price is Free: the viewer's own plan ("Current plan") or
 *      the entry point for a new account ("Start free").
 *   3. Anyone not signed in gets a register link.
 *   4. The tier the viewer is already on is disabled.
 *   5. Moving down a tier, or up while already paying, is a plan *change* on
 *      the existing subscription — never a second checkout, which would
 *      double-bill.
 *   6. Moving up from Free is the one case that starts a new subscription via
 *      checkout.
 *
 * `disabled` is only ever true for `current`; the page owns the transient
 * "busy" disabled state so the pure decision stays free of request state.
 */
export function planCta({ plan, currentTier, isAuthenticated }: PlanCtaInput): PlanCta {
  const name = plan.name.trim() || tierLabel(plan.tier);

  // Enterprise is not self-serve, so it is decided before anything else —
  // including for an account that is already on it.
  if (tierRank(plan.tier) === 3) {
    return { kind: "contact", label: "Contact sales", disabled: false };
  }

  const currentRank = currentTier == null ? null : tierRank(currentTier);
  const targetRank = tierRank(plan.tier);

  // A `null` price is Free. Free has no subscription to change, so its only
  // two states are "this is your plan" and "start here".
  if (plan.price_monthly_usd == null) {
    if (isAuthenticated && currentRank !== null && currentRank === targetRank) {
      return { kind: "current", label: "Current plan", disabled: true };
    }
    return { kind: "register", label: "Start free", disabled: false };
  }

  if (!isAuthenticated) {
    return { kind: "register", label: "Get started", disabled: false };
  }

  if (currentRank !== null && currentRank === targetRank) {
    return { kind: "current", label: "Current plan", disabled: true };
  }

  if (currentRank !== null && targetRank < currentRank) {
    return { kind: "change-plan", label: `Switch to ${name}`, disabled: false };
  }

  // Target ranks higher. An unknown current tier (the status read has not
  // answered) is treated as a Free viewer, which is also the only case that
  // starts a new subscription rather than changing the existing one.
  if (currentRank === null || currentRank === 0) {
    return { kind: "checkout", label: `Upgrade to ${name}`, disabled: false };
  }
  return { kind: "change-plan", label: `Switch to ${name}`, disabled: false };
}

/* ------------------------------------------------------------------ */
/* Plan card stats                                                     */
/* ------------------------------------------------------------------ */

export interface PlanStat {
  label: string;
  value: string;
}

/**
 * The structured stat row at the top of a plan card.
 *
 * Only facts the payload actually carries. `monthly_credits === null` is the
 * API's way of saying unlimited (it has no integer for "uncapped"), so it reads
 * as "Unlimited"; `api_calls_month` is additive and a missing value is omitted
 * rather than rendered as a fabricated zero.
 */
export function planStats(plan: SubscriptionPlanResponse): PlanStat[] {
  const stats: PlanStat[] = [
    { label: "Storage", value: `${formatCount(plan.storage_gb)} GB` },
    {
      label: "Conversions / mo",
      value:
        plan.monthly_credits == null ? "Unlimited" : formatCount(plan.monthly_credits),
    },
  ];

  if (plan.api_calls_month != null) {
    stats.push({
      label: "API calls / mo",
      value: formatCount(plan.api_calls_month),
    });
  }

  return stats;
}

/* ------------------------------------------------------------------ */
/* Credit packs                                                        */
/* ------------------------------------------------------------------ */

/**
 * The index of the cheapest pack per credit, or `-1` for an empty list.
 *
 * Ties resolve to the first pack so the badge is stable across renders. A
 * non-finite price (a malformed payload) is ignored rather than winning.
 */
export function bestValueIndex(packs: CreditPricingResponse[]): number {
  let best = -1;
  let bestPrice = Number.POSITIVE_INFINITY;
  packs.forEach((pack, index) => {
    const price = pack.price_per_credit;
    if (Number.isFinite(price) && price < bestPrice) {
      bestPrice = price;
      best = index;
    }
  });
  return best;
}

export type CreditPackCtaKind = "register" | "purchase";

export interface CreditPackCta {
  kind: CreditPackCtaKind;
  label: string;
}

/**
 * What a credit-pack button does: buy for a signed-in account, otherwise start
 * one. A guest cannot purchase, so they are sent to register first.
 */
export function creditPackCta({ isAuthenticated }: { isAuthenticated: boolean }): CreditPackCta {
  return isAuthenticated
    ? { kind: "purchase", label: "Buy credits" }
    : { kind: "register", label: "Get started" };
}
