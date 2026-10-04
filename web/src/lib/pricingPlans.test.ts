// Tests for the plan-card CTA, the card stat row and the credit-pack rules.
// These are the page's decisions, so they live here in Node rather than in a
// DOM test.

import { describe, expect, it } from "vitest";
import type { CreditPricingResponse, SubscriptionPlanResponse } from "@/api/types";
import {
  bestValueIndex,
  creditPackCta,
  planCta,
  planStats,
} from "@/lib/pricingPlans";

function plan(overrides: Partial<SubscriptionPlanResponse> = {}): SubscriptionPlanResponse {
  return {
    tier: "PRO",
    name: "Pro",
    price_monthly_usd: 9.99,
    storage_gb: 50,
    monthly_credits: 500,
    features: [],
    ai: null,
    api_calls_month: 100,
    priority_processing: false,
    support_level: "Priority",
    ...overrides,
  };
}

function pack(credits: number, price_per_credit: number): CreditPricingResponse {
  return { credits, price_usd: credits * price_per_credit, price_per_credit };
}

describe("planCta", () => {
  it("sends Enterprise to sales for everyone", () => {
    const enterprise = plan({
      tier: "ENTERPRISE",
      name: "Enterprise",
      price_monthly_usd: null,
    });
    expect(
      planCta({ plan: enterprise, currentTier: "ENTERPRISE", isAuthenticated: true }),
    ).toEqual({ kind: "contact", label: "Contact sales", disabled: false });
    expect(
      planCta({ plan: enterprise, currentTier: null, isAuthenticated: false }).kind,
    ).toBe("contact");
  });

  it("offers Free to a guest as Start free", () => {
    const free = plan({ tier: "FREE", name: "Free", price_monthly_usd: null });
    expect(planCta({ plan: free, currentTier: null, isAuthenticated: false })).toEqual({
      kind: "register",
      label: "Start free",
      disabled: false,
    });
  });

  it("marks Free as the current plan for a free account", () => {
    const free = plan({ tier: "FREE", name: "Free", price_monthly_usd: null });
    expect(planCta({ plan: free, currentTier: "FREE", isAuthenticated: true })).toEqual({
      kind: "current",
      label: "Current plan",
      disabled: true,
    });
  });

  it("does not call Free the current plan for a paid account", () => {
    const free = plan({ tier: "FREE", name: "Free", price_monthly_usd: null });
    expect(planCta({ plan: free, currentTier: "PRO", isAuthenticated: true }).kind).toBe(
      "register",
    );
  });

  it("offers a paid plan to a guest as Get started", () => {
    expect(planCta({ plan: plan(), currentTier: null, isAuthenticated: false })).toEqual({
      kind: "register",
      label: "Get started",
      disabled: false,
    });
  });

  it("marks the account's current paid plan as current", () => {
    expect(planCta({ plan: plan(), currentTier: "PRO", isAuthenticated: true })).toEqual({
      kind: "current",
      label: "Current plan",
      disabled: true,
    });
  });

  it("treats the legacy PREMIUM alias as the PRO plan", () => {
    expect(
      planCta({ plan: plan(), currentTier: "PREMIUM", isAuthenticated: true }).kind,
    ).toBe("current");
  });

  it("routes a free account to checkout for an upgrade", () => {
    expect(planCta({ plan: plan(), currentTier: "FREE", isAuthenticated: true })).toEqual({
      kind: "checkout",
      label: "Upgrade to Pro",
      disabled: false,
    });
  });

  it("changes the plan for a paid account upgrading", () => {
    const proPlus = plan({ tier: "PRO_PLUS", name: "Pro Plus", price_monthly_usd: 24.99 });
    expect(
      planCta({ plan: proPlus, currentTier: "PRO", isAuthenticated: true }),
    ).toEqual({
      kind: "change-plan",
      label: "Switch to Pro Plus",
      disabled: false,
    });
  });

  it("changes the plan for a downgrade", () => {
    expect(
      planCta({ plan: plan(), currentTier: "PRO_PLUS", isAuthenticated: true }),
    ).toEqual({ kind: "change-plan", label: "Switch to Pro", disabled: false });
  });

  it("treats an unknown current tier as checkout, not a plan change", () => {
    expect(
      planCta({ plan: plan(), currentTier: null, isAuthenticated: true }).kind,
    ).toBe("checkout");
  });

  it("falls back to a readable tier label when the plan has no name", () => {
    expect(
      planCta({
        plan: plan({ name: "  " }),
        currentTier: "FREE",
        isAuthenticated: true,
      }).label,
    ).toBe("Upgrade to Pro");
  });
});

describe("planStats", () => {
  it("lists storage, conversions and API calls from the payload", () => {
    expect(
      planStats(plan({ storage_gb: 50, monthly_credits: 500, api_calls_month: 100 })),
    ).toEqual([
      { label: "Storage", value: "50 GB" },
      { label: "Conversions / mo", value: "500" },
      { label: "API calls / mo", value: "100" },
    ]);
  });

  it("renders null monthly credits as Unlimited", () => {
    const stats = planStats(plan({ monthly_credits: null }));
    expect(stats.find((s) => s.label === "Conversions / mo")?.value).toBe("Unlimited");
  });

  it("omits the API-calls stat when the API did not send it", () => {
    expect(planStats(plan({ api_calls_month: null })).map((s) => s.label)).toEqual([
      "Storage",
      "Conversions / mo",
    ]);
  });

  it("groups thousands", () => {
    expect(
      planStats(plan({ storage_gb: 1000, monthly_credits: 2000 })).map((s) => s.value),
    ).toEqual(["1,000 GB", "2,000", "100"]);
  });
});

describe("bestValueIndex", () => {
  it("picks the pack with the lowest price per credit", () => {
    expect(bestValueIndex([pack(100, 0.1), pack(5000, 0.06), pack(500, 0.08)])).toBe(1);
  });

  it("picks the first on a tie", () => {
    expect(bestValueIndex([pack(100, 0.06), pack(500, 0.06)])).toBe(0);
  });

  it("returns -1 for no packs", () => {
    expect(bestValueIndex([])).toBe(-1);
  });

  it("ignores a malformed price", () => {
    expect(bestValueIndex([pack(100, Number.NaN), pack(500, 0.08)])).toBe(1);
  });
});

describe("creditPackCta", () => {
  it("sends guests to register", () => {
    expect(creditPackCta({ isAuthenticated: false })).toEqual({
      kind: "register",
      label: "Get started",
    });
  });

  it("lets an authenticated account buy", () => {
    expect(creditPackCta({ isAuthenticated: true })).toEqual({
      kind: "purchase",
      label: "Buy credits",
    });
  });
});
