/**
 * Rules for changing an existing subscription's plan inside our own UI.
 *
 * Replaces the Customer Portal's "change plan" screen, which cannot be branded.
 * Deliberately pure and free of React: this repo's test environment is Node
 * (no jsdom), so anything that decides *behaviour* — which action to offer,
 * what to say afterwards, where to send someone on a failure — has to live
 * outside a component to be testable. The component keeps only the request and
 * the rendering.
 */

import { formatDateOrNull } from "@/lib/format";
import type { ChangePlanResponse } from "@/api/types";

/**
 * Tiers a customer can move between without talking to sales, cheapest first.
 *
 * `ENTERPRISE` is absent on purpose: it is not self-serve, and the API answers
 * a change to it with 400. `FREE` is absent too — moving to it is
 * `cancelSubscription`, not a plan change.
 */
export const SELF_SERVE_TIERS = ["PRO", "PRO_PLUS"] as const;
export type SelfServeTier = (typeof SELF_SERVE_TIERS)[number];

/**
 * Ordering used to tell an upgrade from a downgrade.
 *
 * `PREMIUM` is ranked with `PRO` because the domain keeps it as a legacy alias
 * (see the API's own `_TIER_RANK`).
 */
const TIER_RANK: Record<string, number> = {
  FREE: 0,
  GUEST: 0,
  PREMIUM: 1,
  PRO: 1,
  PRO_PLUS: 2,
  ENTERPRISE: 3,
};

function normaliseTier(tier: string | null | undefined): string {
  return (tier ?? "").trim().toUpperCase();
}

/** Where a tier sits in the plan order; unknown values rank as Free. */
export function tierRank(tier: string | null | undefined): number {
  return TIER_RANK[normaliseTier(tier)] ?? 0;
}

/**
 * The rank of the most expensive plan a customer can buy without sales.
 *
 * Derived from `SELF_SERVE_TIERS` rather than written as the literal `2`, so
 * that adding a third self-serve tier moves the "nothing above this" boundary
 * along with it instead of leaving it behind at the old plan.
 */
const TOP_SELF_SERVE_RANK = Math.max(...SELF_SERVE_TIERS.map((tier) => tierRank(tier)));

/** A display name for a tier enum value: `"PRO_PLUS"` → `"Pro Plus"`. */
export function tierLabel(tier: string | null | undefined): string {
  const value = (tier ?? "").trim();
  if (!value) return "Free";
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

function isSelfServeTier(tier: string): tier is SelfServeTier {
  return (SELF_SERVE_TIERS as readonly string[]).includes(tier);
}

/**
 * Whether `tier` is the most expensive plan a customer can buy without sales.
 *
 * The billing page treats this tier differently from the ones below it: there is
 * no upgrade left to sell, so an in-page Change plan section could only ever
 * hold downgrades, and the hero's button sends the customer to `/pricing` to
 * compare instead. Enterprise answers false — it sits *above* this tier and is
 * not self-serve at all, which is a different situation with a different action
 * (contact sales).
 */
export function isTopSelfServeTier(tier: string | null | undefined): boolean {
  const current = normaliseTier(tier);
  if (!isSelfServeTier(current)) return false;
  return tierRank(current) === TOP_SELF_SERVE_RANK;
}

export interface PlanChangeOption {
  tier: SelfServeTier;
  label: string;
  direction: "upgrade" | "downgrade";
}

/**
 * Whether `changePlan` should be offered to move `currentTier` → `targetTier`.
 *
 * False for a customer with no subscription (rank 0 — they must start one via
 * checkout, which is what the API's 409 says) and for an `ENTERPRISE` account
 * (not self-serve). Also false for the tier they are already on, so the action
 * for their current plan can be rendered disabled rather than offered.
 */
export function canChangePlanTo(
  currentTier: string | null | undefined,
  targetTier: string | null | undefined,
): boolean {
  const current = normaliseTier(currentTier);
  const target = normaliseTier(targetTier);
  if (!isSelfServeTier(target)) return false;
  const rank = tierRank(current);
  // Free/Guest (no subscription) and Enterprise (not self-serve).
  if (rank === 0 || rank === 3) return false;
  return target !== current;
}

/**
 * The plan changes worth offering in the billing page's Change plan section.
 *
 * Empty when there is nothing to offer — a Free account (no subscription to
 * change), an Enterprise one (not self-serve), or the top self-serve plan (see
 * `isTopSelfServeTier`, which has nothing above it). Empty is the signal for
 * the billing page to omit the section entirely rather than render dead
 * buttons, and for the hero's "Change plan" button to send the customer to
 * `/pricing` instead of scrolling to a section that is not there.
 */
export function planChangeOptions(
  currentTier: string | null | undefined,
): PlanChangeOption[] {
  const current = normaliseTier(currentTier);
  const rank = tierRank(current);
  if (rank === 0 || rank === 3) return [];
  // The top self-serve plan: an upgrade is impossible, and its downgrade is
  // offered on the pricing page (and by the retention flow) rather than here.
  if (isTopSelfServeTier(current)) return [];
  return SELF_SERVE_TIERS.filter((tier) => tier !== current).map((tier) => ({
    tier,
    label: tierLabel(tier),
    direction: tierRank(tier) > rank ? "upgrade" : "downgrade",
  }));
}

/**
 * Where the plan hero's primary button sends the customer.
 *
 * `pricing` compares plans on the public pricing page, `change-plan-section`
 * scrolls down to the in-page Change plan card, and `support` is for an
 * Enterprise account, which is not self-serve.
 */
export type PlanHeroTarget = "pricing" | "change-plan-section" | "support";

export interface PlanHeroCta {
  label: string;
  target: PlanHeroTarget;
}

/**
 * The plan hero's single primary action, for every tier.
 *
 * The same shape as `planCta` on the pricing page, and for the same reason: a
 * decision about where a button leads has to be testable, and this repo's Node
 * test environment cannot click one. The rule it encodes is that the button
 * always leads somewhere real —
 *   - Free/Guest: the pricing page, because there is no subscription to change
 *     yet;
 *   - PRO: the Change plan section below, where the upgrade to Pro Plus waits;
 *   - PRO_PLUS: the pricing page, because nothing sits above it and its own
 *     downgrade is offered there rather than in a section of its own (see
 *     `isTopSelfServeTier`);
 *   - Enterprise: support, because it is not self-serve.
 */
export function planHeroCta(tier: string | null | undefined): PlanHeroCta {
  const rank = tierRank(tier);
  if (rank === 0) return { label: "Choose a plan", target: "pricing" };
  if (rank >= 3) return { label: "Contact us", target: "support" };
  if (planChangeOptions(tier).length > 0) {
    return { label: "Change plan", target: "change-plan-section" };
  }
  return { label: "Change plan", target: "pricing" };
}

export interface PlanChangeFeedback {
  kind: "upgrade" | "downgrade";
  title: string;
  detail: string;
}

/**
 * The explanation shown above the Change plan card's buttons.
 *
 * Derived from the options on screen rather than written once. The card offers
 * only upgrades now (Pro Plus, the one tier with a downgrade, no longer gets the
 * section), and a paragraph promising that "downgrades start at the end of your
 * billing period" next to a single Upgrade button describes a control the
 * customer cannot see. Building the copy from the same list that renders the
 * buttons means it cannot outlive them.
 */
export function planChangeExplainer(options: readonly PlanChangeOption[]): string {
  const parts: string[] = [];

  if (options.some((option) => option.direction === "upgrade")) {
    parts.push(
      "Upgrades take effect immediately and are prorated — you're only charged the difference for the rest of this period, and any unspent plan credits carry over to the new plan.",
    );
  }

  if (options.some((option) => option.direction === "downgrade")) {
    parts.push(
      "Downgrades start at the end of your current billing period, so you keep what you already paid for.",
    );
  }

  return parts.join(" ");
}

function creditWord(count: number): string {
  return count === 1 ? "credit" : "credits";
}

/** `expire`/`expires`, so a single carried credit reads as a sentence. */
function expireVerb(count: number): string {
  return count === 1 ? "expires" : "expire";
}

/**
 * Turn the API's plan-change result into the confirmation to show.
 *
 * The carryover sentence is built from the response's own
 * `carryover_credits` and `carryover_expires_at` — never a hardcoded number —
 * because that is money the customer cares about, and it is the only part of an
 * upgrade they cannot see in the plan name. The expiry is formatted as a local
 * instant (`formatDateOrNull`), so a UTC midnight cannot be printed as the
 * wrong calendar day.
 *
 * When the API did not report an expiry the sentence stays true without one
 * rather than inventing a date.
 */
export function describePlanChange(result: ChangePlanResponse): PlanChangeFeedback {
  const target = tierLabel(result.tier);

  // The direction comes from the tiers, not from whether `scheduled_effective_at`
  // happens to be set: a scheduled downgrade always carries one, but the
  // response is still a downgrade if it does not, and treating that as an
  // upgrade would announce a plan the account is not on yet.
  if (tierRank(result.tier) < tierRank(result.previous_tier)) {
    const when = formatDateOrNull(result.scheduled_effective_at);
    const keep = tierLabel(result.previous_tier);
    return {
      kind: "downgrade",
      title: `Downgrade to ${target} scheduled`,
      detail: when
        ? `You'll stay on ${keep} until ${when}, then move to ${target}.`
        : `You'll stay on ${keep} until the end of your current billing period, then move to ${target}.`,
    };
  }

  if (result.carryover_credits > 0) {
    const expiry = formatDateOrNull(result.carryover_expires_at);
    const count = result.carryover_credits;
    return {
      kind: "upgrade",
      title: `You're now on ${target}`,
      detail: expiry
        ? `${count} unspent ${creditWord(count)} carried over and ${expireVerb(count)} on ${expiry}.`
        : `${count} unspent ${creditWord(count)} carried over to your new plan.`,
    };
  }

  return {
    kind: "upgrade",
    title: `You're now on ${target}`,
    detail:
      result.plan_credits > 0
        ? `Your new plan includes ${result.plan_credits} ${creditWord(result.plan_credits)}.`
        : result.message || `Your plan is now ${target}.`,
  };
}

export interface PlanChangeFailure {
  /**
   * True when the account has no subscription to change (HTTP 409), so the
   * right recovery is to pick a plan — the billing page sends them to
   * `/pricing`, mirroring how it already handles a 404 from the portal.
   */
  goToPricing: boolean;
  message: string;
}

/**
 * How to recover from a failed plan change.
 *
 * 409 is the "no subscription to change" case the API documents for a Free
 * account; 400 covers the same tier and the non-self-serve Enterprise target.
 * Both read the status rather than the server's wording so a copy change on the
 * API cannot silently break the routing.
 */
export function planChangeFailure(status: number, serverMessage: string): PlanChangeFailure {
  if (status === 409) {
    return {
      goToPricing: true,
      message: "You don't have a subscription yet. Pick a plan to get started.",
    };
  }
  if (status === 400) {
    return {
      goToPricing: false,
      message: serverMessage || "That plan change isn't available for your account.",
    };
  }
  return {
    goToPricing: false,
    message: serverMessage || "Could not change your plan. Please try again.",
  };
}
