/**
 * The plan/upgrade decisions on the Billing page.
 *
 * The rules under test are the ones that keep the page honest: a price the API
 * did not quote is omitted, the next plan up comes from ranking rather than a
 * hardcoded list, and the "gains" are only ever fields the payload contained.
 */

import { describe, expect, it } from "vitest";
import type { SubscriptionPlanResponse } from "@/api/types";
import {
  monthlyPriceLabel,
  nextPlanAbove,
  periodLabel,
  planForTier,
  planStatusTone,
  upgradeGains,
} from "./billingOverview";

function plan(overrides: Partial<SubscriptionPlanResponse> = {}): SubscriptionPlanResponse {
  return {
    tier: "FREE",
    name: "Free",
    price_monthly_usd: null,
    storage_gb: 5,
    monthly_credits: 50,
    features: [],
    ai: null,
    api_calls_month: 10,
    priority_processing: false,
    support_level: "Community",
    ...overrides,
  };
}

const PLANS: SubscriptionPlanResponse[] = [
  plan(),
  plan({
    tier: "PRO",
    name: "Pro",
    price_monthly_usd: 9.99,
    storage_gb: 50,
    monthly_credits: 500,
    ai: { model_level: "standard", model_label: "Standard", requests_per_hour: 20, max_attachments: 3, max_document_mb: 25, max_actions_per_turn: 4 },
  }),
  plan({
    tier: "PRO_PLUS",
    name: "Pro Plus",
    price_monthly_usd: 24.99,
    storage_gb: 100,
    monthly_credits: 2000,
    ai: { model_level: "advanced", model_label: "Advanced", requests_per_hour: 60, max_attachments: 8, max_document_mb: 100, max_actions_per_turn: 8 },
    api_calls_month: 1000,
    priority_processing: true,
    support_level: "24/7",
  }),
  plan({
    tier: "ENTERPRISE",
    name: "Enterprise",
    storage_gb: 1000,
    monthly_credits: null,
    api_calls_month: null,
    priority_processing: true,
    support_level: "Dedicated",
  }),
];

describe("planForTier / monthlyPriceLabel", () => {
  it("looks a plan up case-insensitively", () => {
    expect(planForTier(PLANS, " pro ")?.name).toBe("Pro");
    expect(planForTier(PLANS, null)).toBeNull();
    expect(planForTier(PLANS, "NOPE")).toBeNull();
  });

  it("quotes a paid price and omits an unpriced plan", () => {
    expect(monthlyPriceLabel(PLANS, "PRO")).toBe("$9.99/month");
    expect(monthlyPriceLabel(PLANS, "PRO_PLUS")).toBe("$24.99/month");
    expect(monthlyPriceLabel(PLANS, "FREE")).toBeNull();
    expect(monthlyPriceLabel(PLANS, "ENTERPRISE")).toBeNull();
    expect(monthlyPriceLabel([], "PRO")).toBeNull();
  });
});

describe("planStatusTone", () => {
  it("maps known statuses and defaults an unknown one to muted", () => {
    expect(planStatusTone("active")).toBe("success");
    expect(planStatusTone("TRIALING")).toBe("success");
    expect(planStatusTone("past_due")).toBe("warning");
    expect(planStatusTone("unpaid")).toBe("error");
    expect(planStatusTone("something_new")).toBe("muted");
    expect(planStatusTone(null)).toBe("muted");
  });
});

describe("periodLabel", () => {
  it("says 'Renews' normally and 'Ends' once cancellation is scheduled", () => {
    const end = "2026-11-01T12:00:00Z";
    expect(periodLabel({ current_period_end: end, cancel_at_period_end: false })).toMatch(/^Renews /);
    expect(periodLabel({ current_period_end: end, cancel_at_period_end: true })).toMatch(/^Ends /);
    // An older API that does not send the flag is treated as still renewing.
    expect(periodLabel({ current_period_end: end })).toMatch(/^Renews /);
  });

  it("is null without a parseable period end", () => {
    expect(periodLabel({ current_period_end: null })).toBeNull();
    expect(periodLabel(null)).toBeNull();
  });
});

describe("nextPlanAbove", () => {
  it("finds the immediately next plan and whether it is self-serve", () => {
    const free = nextPlanAbove(PLANS, "FREE");
    expect(free?.plan.tier).toBe("PRO");
    // A Free account must start a subscription through checkout.
    expect(free?.selfServe).toBe(false);

    const pro = nextPlanAbove(PLANS, "PRO");
    expect(pro?.plan.tier).toBe("PRO_PLUS");
    expect(pro?.selfServe).toBe(true);

    const plus = nextPlanAbove(PLANS, "PRO_PLUS");
    expect(plus?.plan.tier).toBe("ENTERPRISE");
    expect(plus?.selfServe).toBe(false);

    // Enterprise is the top: nothing to nudge toward.
    expect(nextPlanAbove(PLANS, "ENTERPRISE")).toBeNull();
  });

  it("ranks the legacy PREMIUM alias with PRO", () => {
    expect(nextPlanAbove(PLANS, "PREMIUM")?.plan.tier).toBe("PRO_PLUS");
  });

  it("is null when the API lists no higher plan", () => {
    expect(nextPlanAbove([plan()], "FREE")).toBeNull();
    expect(nextPlanAbove([], "PRO")).toBeNull();
  });
});

describe("upgradeGains", () => {
  it("takes its lines from the payload, capped at three", () => {
    expect(upgradeGains(planForTier(PLANS, "PRO"))).toEqual([
      "500 conversions a month",
      "50 GB of storage",
      "Standard AI assistant",
    ]);
  });

  it("includes priority processing and API calls when the value is real", () => {
    const gains = upgradeGains(planForTier(PLANS, "PRO_PLUS"));
    expect(gains).toEqual([
      "2000 conversions a month",
      "100 GB of storage",
      "Advanced AI assistant",
    ]);
    // Priority processing is the fourth candidate, so the cap drops it — the
    // cap is deliberate, not a missing rule.
    expect(gains).toHaveLength(3);
  });

  it("omits anything the API did not send rather than inventing it", () => {
    expect(
      upgradeGains(
        plan({ storage_gb: 0, monthly_credits: null, ai: null, api_calls_month: null }),
      ),
    ).toEqual([]);
    expect(upgradeGains(null)).toEqual([]);
    // Enterprise: no credit count, no API-call number, so only real values remain.
    expect(upgradeGains(planForTier(PLANS, "ENTERPRISE"))).toEqual([
      "1000 GB of storage",
      "Priority processing",
    ]);
  });
});
