/**
 * The retention flow behind the cancel button.
 *
 * The rules under test are the ones a customer's money depends on: which save
 * offer a reason produces, whether a proposed plan change is actually possible,
 * what the final screen promises about credits, and the fact that "Something
 * else" never shows an empty offer screen.
 */

import { describe, expect, it } from "vitest";
import type { SubscriptionPlanResponse } from "@/api/types";
import {
  CANCEL_REASONS,
  cancellationOutcome,
  cancelReasonLabel,
  cancelStepCount,
  cancelStepNumber,
  cancelSteps,
  canLeaveReasonStep,
  cheaperSelfServePlan,
  isCancelReason,
  nextCancelStep,
  previousCancelStep,
  saveOfferFor,
} from "./cancelFlow";

function plan(overrides: Partial<SubscriptionPlanResponse> = {}): SubscriptionPlanResponse {
  return {
    tier: "PRO",
    name: "Pro",
    price_monthly_usd: 9.99,
    storage_gb: 50,
    monthly_credits: 500,
    features: [],
    ai: null,
    ...overrides,
  };
}

const PLANS: SubscriptionPlanResponse[] = [
  plan({ tier: "FREE", name: "Free", price_monthly_usd: null, storage_gb: 5, monthly_credits: 50 }),
  plan(),
  plan({ tier: "PRO_PLUS", name: "Pro Plus", price_monthly_usd: 24.99, storage_gb: 100, monthly_credits: 2000 }),
  plan({ tier: "ENTERPRISE", name: "Enterprise", price_monthly_usd: null, storage_gb: 1000, monthly_credits: null }),
];

describe("CANCEL_REASONS", () => {
  it("offers five reasons with an open-ended last one", () => {
    expect(CANCEL_REASONS).toHaveLength(5);
    expect(CANCEL_REASONS.map((r) => r.id)).toEqual([
      "too_expensive",
      "not_using",
      "missing_feature",
      "switching",
      "other",
    ]);
    expect(CANCEL_REASONS.every((r) => r.label.length > 0)).toBe(true);
    expect(isCancelReason("other")).toBe(true);
    expect(isCancelReason("nonsense")).toBe(false);
    expect(isCancelReason(null)).toBe(false);
    expect(cancelReasonLabel("not_using")).toBe("I'm not using it enough");
  });
});

describe("cheaperSelfServePlan", () => {
  it("finds the closest cheaper paid plan", () => {
    expect(cheaperSelfServePlan("PRO_PLUS", PLANS)?.tier).toBe("PRO");
  });

  it("proposes nothing from the cheapest paid tier or from Free", () => {
    // Free would be a cancellation, not a plan change.
    expect(cheaperSelfServePlan("PRO", PLANS)).toBeNull();
    expect(cheaperSelfServePlan("FREE", PLANS)).toBeNull();
    expect(cheaperSelfServePlan(null, PLANS)).toBeNull();
    expect(cheaperSelfServePlan("PRO_PLUS", [])).toBeNull();
  });
});

describe("saveOfferFor", () => {
  it("proposes a real downgrade for 'too expensive' when one exists", () => {
    const offer = saveOfferFor("too_expensive", { currentTier: "PRO_PLUS", plans: PLANS });
    expect(offer.kind).toBe("change-plan");
    if (offer.kind !== "change-plan") throw new Error("expected a change-plan offer");
    expect(offer.tier).toBe("PRO");
    expect(offer.cta).toBe("Switch to Pro");
    expect(offer.body).toContain("$9.99 a month");
    expect(offer.body).toContain("500 conversions a month");
    expect(offer.body).toContain("credits you've bought");
  });

  it("explains Free instead when the account is already cheapest", () => {
    const offer = saveOfferFor("too_expensive", { currentTier: "PRO", plans: PLANS });
    expect(offer.kind).toBe("free");
    if (offer.kind !== "free") throw new Error("expected a free offer");
    expect(offer.body).toContain("50 conversions a month");
    expect(offer.body).toContain("5 GB of storage");
    expect(offer.body).toContain("never expire");
    expect(offer.cta).toBe("Keep my plan");
  });

  it("explains Free and credit survival for 'not using it'", () => {
    const offer = saveOfferFor("not_using", { currentTier: "PRO_PLUS", plans: PLANS });
    expect(offer.kind).toBe("free");
    if (offer.kind !== "free") throw new Error("expected a free offer");
    expect(offer.body).toContain("50 conversions a month");
    expect(offer.body).toContain("purchased credits never expire");
  });

  it("routes a missing feature and a switch to support", () => {
    expect(saveOfferFor("missing_feature", { currentTier: "PRO", plans: PLANS }).kind).toBe("support");
    const switching = saveOfferFor("switching", { currentTier: "PRO", plans: PLANS });
    expect(switching.kind).toBe("support");
    if (switching.kind !== "support") throw new Error("expected a support offer");
    expect(switching.body).toContain("switching to");
  });

  it("has no offer for 'something else'", () => {
    expect(saveOfferFor("other", { currentTier: "PRO", plans: PLANS })).toEqual({ kind: "none" });
  });

  it("does not quote figures the API never sent", () => {
    const offer = saveOfferFor("not_using", { currentTier: "PRO", plans: [] });
    expect(offer.kind).toBe("free");
    if (offer.kind !== "free") throw new Error("expected a free offer");
    expect(offer.body).not.toMatch(/\d/);
  });
});

describe("cancellationOutcome", () => {
  it("states the access end date, the credit stop, and what is kept", () => {
    const outcome = cancellationOutcome({
      currentPeriodEnd: "2026-11-01T12:00:00Z",
      planCredits: 120,
      carryoverCredits: 300,
      purchasedCredits: 1000,
    });
    expect(outcome.accessEndsLabel).toBeTruthy();
    expect(outcome.headline).toBe(`Your plan ends on ${outcome.accessEndsLabel}`);
    expect(outcome.lines[0]).toContain("You keep everything until");
    expect(outcome.lines[1]).toBe("120 plan credits stop renewing.");
    expect(outcome.lines[2]).toBe("300 carried-over credits stop renewing.");
    expect(outcome.lines[3]).toBe("1000 purchased credits stay on your account — they never expire.");
  });

  it("stays true without knowing the counts or the date", () => {
    const outcome = cancellationOutcome({ currentPeriodEnd: null });
    expect(outcome.accessEndsLabel).toBeNull();
    expect(outcome.headline).toBe("Your plan ends at the end of the current billing period");
    expect(outcome.lines).toEqual([
      "You keep everything until the end of the current billing period.",
      "Your monthly plan credits stop renewing.",
      "Purchased credits stay on your account — they never expire.",
    ]);
  });

  it("omits the carryover line when there is no carryover", () => {
    const outcome = cancellationOutcome({
      currentPeriodEnd: "2026-11-01T12:00:00Z",
      carryoverCredits: 0,
    });
    expect(outcome.lines.some((l) => l.includes("carried-over"))).toBe(false);
  });
});

describe("step rules", () => {
  it("skips the offer step only for 'something else'", () => {
    expect(cancelSteps("too_expensive")).toEqual(["reason", "offer", "confirm"]);
    expect(cancelSteps("other")).toEqual(["reason", "confirm"]);
    expect(cancelStepCount("other")).toBe(2);
    expect(cancelStepNumber("confirm", "other")).toBe(2);
  });

  it("blocks leaving the reason step until a reason is chosen", () => {
    expect(canLeaveReasonStep(null)).toBe(false);
    expect(nextCancelStep("reason", null)).toBeNull();
    expect(nextCancelStep("reason", "not_using")).toBe("offer");
    expect(nextCancelStep("reason", "other")).toBe("confirm");
  });

  it("walks forward and backward along the chosen path", () => {
    expect(nextCancelStep("offer", "not_using")).toBe("confirm");
    expect(nextCancelStep("confirm", "not_using")).toBeNull();
    expect(previousCancelStep("confirm", "not_using")).toBe("offer");
    expect(previousCancelStep("reason", "not_using")).toBeNull();
    // A step that is not on this reason's path has nowhere to go.
    expect(nextCancelStep("offer", "other")).toBeNull();
  });
});
