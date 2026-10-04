/**
 * The retention flow behind the cancel button.
 *
 * Cancelling is the one irreversible-ish action on the Billing page, so it is
 * placed last and painted quietly, and the click opens a three-step wizard whose
 * whole job is to give the customer a way to stay. Every branch of that wizard —
 * which reasons exist, which save offer a reason produces, what the final screen
 * promises, and how the steps advance — is decided here.
 *
 * Deliberately pure and free of React: this repo's test environment is Node (no
 * jsdom), so anything that decides *behaviour* has to live outside a component
 * to be testable. The component keeps only the request and the rendering.
 */

import { canChangePlanTo, tierLabel, tierRank } from "@/lib/planChange";
import { formatDateOrNull } from "@/lib/format";
import { formatUsd } from "@/lib/creditPacks";
import type { SubscriptionPlanResponse } from "@/api/types";

export type CancelReasonId =
  | "too_expensive"
  | "not_using"
  | "missing_feature"
  | "switching"
  | "other";

export interface CancelReason {
  id: CancelReasonId;
  label: string;
}

/**
 * The reasons offered in the first step, in the order shown.
 *
 * The last one is deliberately open-ended; it is the only reason that skips the
 * save offer entirely, because there is nothing specific to offer against.
 */
export const CANCEL_REASONS: readonly CancelReason[] = [
  { id: "too_expensive", label: "It costs too much" },
  { id: "not_using", label: "I'm not using it enough" },
  { id: "missing_feature", label: "It's missing a feature I need" },
  { id: "switching", label: "I'm switching to another tool" },
  { id: "other", label: "Something else" },
];

export function isCancelReason(value: string | null | undefined): value is CancelReasonId {
  return CANCEL_REASONS.some((reason) => reason.id === value);
}

/** The label for a reason id, for an announcement or a summary line. */
export function cancelReasonLabel(reason: CancelReasonId): string {
  return CANCEL_REASONS.find((r) => r.id === reason)?.label ?? "Something else";
}

/** `["a", "b", "c"]` → `"a, b and c"`; `[]` → `""`. */
function joinWithAnd(parts: readonly string[]): string {
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** The data-backed inclusions of a plan, or `null` when the API sent neither. */
function planInclusions(plan: SubscriptionPlanResponse | null): string | null {
  if (!plan) return null;
  const parts: string[] = [];
  if (typeof plan.monthly_credits === "number" && plan.monthly_credits > 0) {
    parts.push(`${plan.monthly_credits} conversions a month`);
  }
  if (typeof plan.storage_gb === "number" && plan.storage_gb > 0) {
    parts.push(`${plan.storage_gb} GB of storage`);
  }
  return parts.length > 0 ? joinWithAnd(parts) : null;
}

function freeTierPlan(plans: readonly SubscriptionPlanResponse[]): SubscriptionPlanResponse | null {
  return plans.find((p) => p.tier.trim().toUpperCase() === "FREE") ?? null;
}

/**
 * A sentence about what Free includes, built from the Free plan's own payload.
 *
 * When the API did not list a Free plan (or listed one with no numbers) the
 * sentence stays true without a figure rather than quoting the documented "50".
 */
function freeTierCopy(plans: readonly SubscriptionPlanResponse[]): string {
  const free = freeTierPlan(plans);
  const parts: string[] = [];
  if (free && typeof free.monthly_credits === "number" && free.monthly_credits > 0) {
    parts.push(`${free.monthly_credits} conversions a month`);
  }
  if (free && typeof free.storage_gb === "number" && free.storage_gb > 0) {
    parts.push(`${free.storage_gb} GB of storage`);
  }
  return parts.length > 0
    ? `The Free plan includes ${joinWithAnd(parts)}.`
    : "The Free plan is free to use and includes a monthly conversion allowance.";
}

/**
 * The highest self-serve plan below `currentTier`, or `null` when there is none.
 *
 * Uses `canChangePlanTo` so a Free target (which is a cancellation, not a plan
 * change) and Enterprise are never proposed as a downgrade.
 */
export function cheaperSelfServePlan(
  currentTier: string | null | undefined,
  plans: readonly SubscriptionPlanResponse[],
): SubscriptionPlanResponse | null {
  const rank = tierRank(currentTier);
  return (
    plans
      .filter((p) => canChangePlanTo(currentTier, p.tier) && tierRank(p.tier) < rank)
      .sort((a, b) => tierRank(b.tier) - tierRank(a.tier))[0] ?? null
  );
}

export type SaveOffer =
  | {
      kind: "change-plan";
      /** The tier to move to; passed straight to `changePlan`. */
      tier: string;
      title: string;
      body: string;
      cta: string;
    }
  | { kind: "free"; title: string; body: string; cta: string }
  | { kind: "support"; title: string; body: string; cta: string }
  | { kind: "none" };

export interface SaveOfferContext {
  currentTier?: string | null;
  plans?: readonly SubscriptionPlanResponse[];
}

/**
 * The tailored save offer for a reason.
 *
 * "Costs too much" proposes a real, cheaper self-serve plan (and falls back to
 * explaining Free when the account is already on the cheapest paid tier);
 * "not using it enough" explains Free and that purchased credits survive;
 * "missing a feature" and "switching" route to support; "something else" has no
 * offer, which is what makes the wizard skip straight to the confirmation.
 *
 * Every offer's copy is derived from the plan payload. Nothing is quoted that
 * the API did not send.
 */
export function saveOfferFor(
  reason: CancelReasonId,
  ctx: SaveOfferContext = {},
): SaveOffer {
  const plans = ctx.plans ?? [];

  switch (reason) {
    case "too_expensive": {
      const cheaper = cheaperSelfServePlan(ctx.currentTier, plans);
      if (cheaper) {
        const label = tierLabel(cheaper.tier);
        const price = cheaper.price_monthly_usd;
        const priceSentence =
          typeof price === "number" && price > 0
            ? `${label} is ${formatUsd(price)} a month`
            : `${label} costs less`;
        const inclusions = planInclusions(cheaper);
        return {
          kind: "change-plan",
          tier: cheaper.tier,
          title: `Save with ${label}`,
          body: inclusions
            ? `${priceSentence} and still includes ${inclusions}. You keep your account, your files and any credits you've bought.`
            : `${priceSentence}. You keep your account, your files and any credits you've bought.`,
          cta: `Switch to ${label}`,
        };
      }
      return {
        kind: "free",
        title: "You're already on our lowest paid plan",
        body: `${freeTierCopy(plans)} Purchased credits never expire, so nothing you've bought is lost.`,
        cta: "Keep my plan",
      };
    }

    case "not_using":
      return {
        kind: "free",
        title: "Keep your credits, drop the monthly bill",
        body: `${freeTierCopy(plans)} Anything you've bought stays on your account — purchased credits never expire.`,
        cta: "Keep my plan",
      };

    case "missing_feature":
      return {
        kind: "support",
        title: "Tell us what's missing",
        body: "If we're close, a quick note could change your mind. Tell us what you need and we'll look at whether we can add it.",
        cta: "Tell us what's missing",
      };

    case "switching":
      return {
        kind: "support",
        title: "What are you moving to?",
        body: "If another tool does something better, we'd like to know. Tell us what you're switching to so we can improve.",
        cta: "Tell us what's missing",
      };

    case "other":
      return { kind: "none" };
  }
}

export interface CancellationInput {
  currentPeriodEnd?: string | null;
  /** The monthly plan bucket's remaining credits, when the API reported them. */
  planCredits?: number | null;
  carryoverCredits?: number | null;
  purchasedCredits?: number | null;
}

export interface CancellationOutcome {
  /** The formatted date access ends, or `null` when the API did not send one. */
  accessEndsLabel: string | null;
  headline: string;
  /** The exact things that happen, in order. */
  lines: string[];
}

/**
 * Exactly what cancelling does, for the final step.
 *
 * The date comes from the subscription's own `current_period_end`; when the API
 * did not send one the sentence stays true without a date rather than inventing
 * one. The purchased-credits line is always present: it is the fact a
 * cancelling customer most needs, and it is true whether or not the API sent a
 * count.
 */
export function cancellationOutcome(input: CancellationInput): CancellationOutcome {
  const accessEndsLabel = formatDateOrNull(input.currentPeriodEnd);
  const headline = accessEndsLabel
    ? `Your plan ends on ${accessEndsLabel}`
    : "Your plan ends at the end of the current billing period";

  const lines: string[] = [
    accessEndsLabel
      ? `You keep everything until ${accessEndsLabel}.`
      : "You keep everything until the end of the current billing period.",
  ];

  const planCredits = input.planCredits ?? 0;
  lines.push(
    planCredits > 0
      ? `${planCredits} plan credits stop renewing.`
      : "Your monthly plan credits stop renewing.",
  );

  const carryover = input.carryoverCredits ?? 0;
  if (carryover > 0) {
    lines.push(`${carryover} carried-over credits stop renewing.`);
  }

  const purchased = input.purchasedCredits ?? 0;
  lines.push(
    purchased > 0
      ? `${purchased} purchased credits stay on your account — they never expire.`
      : "Purchased credits stay on your account — they never expire.",
  );

  return { accessEndsLabel, headline, lines };
}

/* ------------------------------ Step rules ------------------------------ */

export const CANCEL_STEPS = ["reason", "offer", "confirm"] as const;
export type CancelStep = (typeof CANCEL_STEPS)[number];

/**
 * The steps in order for the chosen reason.
 *
 * "Something else" has no save offer, so its path is reason → confirm with no
 * empty middle screen.
 */
export function cancelSteps(reason: CancelReasonId | null): CancelStep[] {
  return reason === "other" ? ["reason", "confirm"] : [...CANCEL_STEPS];
}

/** Whether the reason step may move on — false until a reason is chosen. */
export function canLeaveReasonStep(reason: CancelReasonId | null): boolean {
  return reason !== null;
}

/** The next step, or `null` at the end (or when the reason step is blocked). */
export function nextCancelStep(
  step: CancelStep,
  reason: CancelReasonId | null,
): CancelStep | null {
  if (step === "reason" && !canLeaveReasonStep(reason)) return null;
  const steps = cancelSteps(reason);
  const index = steps.indexOf(step);
  if (index === -1) return null;
  return steps[index + 1] ?? null;
}

/** The previous step, or `null` at the start. */
export function previousCancelStep(
  step: CancelStep,
  reason: CancelReasonId | null,
): CancelStep | null {
  const steps = cancelSteps(reason);
  const index = steps.indexOf(step);
  if (index <= 0) return null;
  return steps[index - 1];
}

/** 1-based position of a step in the path, for a "Step 1 of 3" label. */
export function cancelStepNumber(step: CancelStep, reason: CancelReasonId | null): number {
  return cancelSteps(reason).indexOf(step) + 1;
}

/** How many steps the chosen reason's path has. */
export function cancelStepCount(reason: CancelReasonId | null): number {
  return cancelSteps(reason).length;
}
