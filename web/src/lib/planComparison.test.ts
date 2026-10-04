// Tests for the feature comparison table's rows. The component only renders
// what this returns, so every derivation rule is pinned here in Node.

import { describe, expect, it } from "vitest";
import type { AiEntitlement, SubscriptionPlanResponse } from "@/api/types";
import { comparisonSections } from "@/lib/planComparison";

function ai(overrides: Partial<AiEntitlement> = {}): AiEntitlement {
  return {
    model_level: "advanced",
    model_label: "Advanced",
    requests_per_hour: 60,
    max_attachments: 3,
    max_document_mb: 25,
    max_actions_per_turn: 8,
    ...overrides,
  };
}

function plan(overrides: Partial<SubscriptionPlanResponse> = {}): SubscriptionPlanResponse {
  return {
    tier: "PRO",
    name: "Pro",
    price_monthly_usd: 9.99,
    storage_gb: 50,
    monthly_credits: 500,
    features: [],
    ai: ai(),
    api_calls_month: 100,
    priority_processing: false,
    support_level: "Priority",
    ...overrides,
  };
}

function rowsFor(plans: SubscriptionPlanResponse[], title: string) {
  return comparisonSections(plans).find((s) => s.title === title)?.rows;
}

describe("comparisonSections", () => {
  it("returns nothing for no plans", () => {
    expect(comparisonSections([])).toEqual([]);
  });

  it("builds the sections in product order", () => {
    expect(comparisonSections([plan()]).map((s) => s.title)).toEqual([
      "Conversions & limits",
      "Storage",
      "Developer API",
      "AI assistant",
      "Support",
    ]);
  });

  it("aligns every row's values with the plan order", () => {
    const plans = [
      plan({
        tier: "FREE",
        name: "Free",
        storage_gb: 5,
        monthly_credits: 50,
        api_calls_month: 10,
        priority_processing: false,
        support_level: "Community",
        ai: null,
      }),
      plan({
        tier: "PRO_PLUS",
        name: "Pro Plus",
        storage_gb: 100,
        monthly_credits: 2000,
        api_calls_month: 1000,
        priority_processing: true,
        support_level: "24/7",
      }),
    ];

    expect(rowsFor(plans, "Storage")?.[0].values).toEqual(["5 GB", "100 GB"]);
    expect(rowsFor(plans, "Conversions & limits")?.[0].values).toEqual([
      "50",
      "2,000",
    ]);
    expect(rowsFor(plans, "Conversions & limits")?.[1].values).toEqual([false, true]);
    expect(rowsFor(plans, "Developer API")?.[0].values).toEqual(["10", "1,000"]);
    expect(rowsFor(plans, "Support")?.[0].values).toEqual(["Community", "24/7"]);
  });

  it("renders unlimited monthly credits", () => {
    expect(
      rowsFor([plan({ monthly_credits: null })], "Conversions & limits")?.[0].values,
    ).toEqual(["Unlimited"]);
  });

  it("reads an uncapped plan's API limit as Unlimited, not as unstated", () => {
    // Enterprise is the uncapped plan: no integer for either cap. Rendering a
    // dash there would claim the plan has no API access, which is the opposite
    // of what its own features list says.
    expect(
      rowsFor([plan({ tier: "ENTERPRISE", monthly_credits: null, api_calls_month: null })], "Developer API")?.[0]
        .values,
    ).toEqual(["Unlimited"]);
  });

  it("keeps a missing API figure as unstated when the plan is still metered", () => {
    // An older API does not send `api_calls_month` at all, but it does send
    // `monthly_credits` — so this must stay unspecified rather than be invented.
    expect(
      rowsFor([plan({ monthly_credits: 500, api_calls_month: null })], "Developer API"),
    ).toBeUndefined();
  });

  it("drops a row whose values are all null", () => {
    const rows = rowsFor(
      [plan({ priority_processing: null })],
      "Conversions & limits",
    );
    expect(rows?.map((r) => r.label)).toEqual(["Monthly conversions"]);
  });

  it("drops a section whose rows are all null", () => {
    const sections = comparisonSections([
      plan({
        ai: null,
        api_calls_month: null,
        priority_processing: null,
        support_level: null,
      }),
    ]);
    expect(sections.map((s) => s.title)).toEqual(["Conversions & limits", "Storage"]);
  });

  it("omits unstated AI counts rather than printing fabricated zeros", () => {
    const rows = rowsFor(
      [
        plan({
          ai: ai({
            requests_per_hour: 0,
            max_attachments: 0,
            max_document_mb: 0,
            max_actions_per_turn: 0,
          }),
        }),
      ],
      "AI assistant",
    );
    expect(rows?.map((r) => r.label)).toEqual(["AI model"]);
    expect(rows?.[0].values).toEqual(["Advanced"]);
  });

  it("keeps a partly known row and leaves unstated cells null", () => {
    const plans = [
      plan({ api_calls_month: 100 }),
      plan({ tier: "ENTERPRISE", name: "Enterprise", api_calls_month: null }),
    ];
    expect(rowsFor(plans, "Developer API")?.[0].values).toEqual(["100", null]);
  });

  it("drops the developer API section for an older payload", () => {
    const sections = comparisonSections([
      plan({
        api_calls_month: null,
        ai: null,
        priority_processing: null,
        support_level: null,
      }),
    ]);
    expect(sections.some((s) => s.title === "Developer API")).toBe(false);
  });
});
