/**
 * The rules behind in-app plan management.
 *
 * These decide which plan changes are offered, what the confirmation says (the
 * carryover is money the customer cares about), and where a failure sends them.
 * All of it is easy to get subtly wrong and impossible to exercise through the
 * component in this repo's Node test environment, which is why it lives here.
 */

import { describe, expect, it } from "vitest";
import { formatDateOrNull } from "@/lib/format";
import type { ChangePlanResponse } from "@/api/types";
import {
  canChangePlanTo,
  describePlanChange,
  isTopSelfServeTier,
  planChangeExplainer,
  planChangeFailure,
  planChangeOptions,
  planHeroCta,
  tierLabel,
  tierRank,
} from "./planChange";

function result(overrides: Partial<ChangePlanResponse> = {}): ChangePlanResponse {
  return {
    tier: "PRO_PLUS",
    previous_tier: "PRO",
    plan_credits: 0,
    carryover_credits: 0,
    carryover_expires_at: null,
    scheduled_effective_at: null,
    message: "",
    ...overrides,
  };
}

describe("tierRank / tierLabel", () => {
  it("orders tiers, ranking the legacy PREMIUM alias with PRO", () => {
    expect(tierRank("FREE")).toBe(0);
    expect(tierRank("PRO")).toBe(1);
    expect(tierRank("PREMIUM")).toBe(1);
    expect(tierRank("PRO_PLUS")).toBe(2);
    expect(tierRank("ENTERPRISE")).toBe(3);
    // Case/whitespace tolerant, and an unknown tier ranks as Free.
    expect(tierRank(" pro_plus ")).toBe(2);
    expect(tierRank("SOMETHING_ELSE")).toBe(0);
    expect(tierRank(null)).toBe(0);
  });

  it("renders a tier enum as a plan name", () => {
    expect(tierLabel("PRO")).toBe("Pro");
    expect(tierLabel("PRO_PLUS")).toBe("Pro Plus");
    expect(tierLabel("ENTERPRISE")).toBe("Enterprise");
    expect(tierLabel("")).toBe("Free");
  });
});

describe("canChangePlanTo", () => {
  it("offers the other self-serve tier at any paid level", () => {
    expect(canChangePlanTo("PRO", "PRO_PLUS")).toBe(true);
    expect(canChangePlanTo("PRO_PLUS", "PRO")).toBe(true);
  });

  it("refuses the tier the account is already on", () => {
    expect(canChangePlanTo("PRO", "PRO")).toBe(false);
    expect(canChangePlanTo("PRO_PLUS", "PRO_PLUS")).toBe(false);
  });

  it("refuses for an account with no subscription", () => {
    // They must start one through checkout; the API answers 409 for this.
    expect(canChangePlanTo("FREE", "PRO")).toBe(false);
    expect(canChangePlanTo(null, "PRO")).toBe(false);
    expect(canChangePlanTo("", "PRO_PLUS")).toBe(false);
  });

  it("refuses a target that is not self-serve", () => {
    expect(canChangePlanTo("PRO", "ENTERPRISE")).toBe(false);
    expect(canChangePlanTo("PRO", "FREE")).toBe(false);
    expect(canChangePlanTo("PRO", "NOT_A_TIER")).toBe(false);
  });

  it("refuses everything from Enterprise", () => {
    expect(canChangePlanTo("ENTERPRISE", "PRO")).toBe(false);
    expect(canChangePlanTo("ENTERPRISE", "PRO_PLUS")).toBe(false);
  });
});

describe("planChangeOptions", () => {
  it("offers an upgrade from PRO", () => {
    expect(planChangeOptions("PRO")).toEqual([
      { tier: "PRO_PLUS", label: "Pro Plus", direction: "upgrade" },
    ]);
  });

  it("offers nothing to a Free account or an Enterprise one", () => {
    // Empty is the signal to omit the section rather than render dead buttons.
    expect(planChangeOptions("FREE")).toEqual([]);
    expect(planChangeOptions(null)).toEqual([]);
    expect(planChangeOptions("ENTERPRISE")).toEqual([]);
  });

  it("offers nothing to Pro Plus: it has no upgrade, and its downgrade lives on /pricing", () => {
    // REVERSED DELIBERATELY. This used to be `[{tier: "PRO", direction:
    // "downgrade"}]`, which rendered a Change plan section holding a single
    // downgrade button. The section is for upgrades now: Pro Plus is the top
    // self-serve plan, so the billing page drops the section for it and the
    // hero's "Change plan" button sends the customer to the pricing page
    // instead, where `planCta` offers "Switch to Pro" against the existing
    // subscription. The retention flow's cheaper-plan offer is unaffected — it
    // uses `canChangePlanTo`, which still answers yes (asserted above).
    expect(planChangeOptions("PRO_PLUS")).toEqual([]);
  });
});

describe("isTopSelfServeTier", () => {
  it("is true only for the most expensive self-serve plan", () => {
    expect(isTopSelfServeTier("PRO_PLUS")).toBe(true);
    // Case/whitespace tolerant, like every other tier helper here.
    expect(isTopSelfServeTier(" pro_plus ")).toBe(true);
  });

  it("is false for the plans below it and for Enterprise", () => {
    expect(isTopSelfServeTier("PRO")).toBe(false);
    expect(isTopSelfServeTier("PREMIUM")).toBe(false);
    expect(isTopSelfServeTier("FREE")).toBe(false);
    expect(isTopSelfServeTier(null)).toBe(false);
    expect(isTopSelfServeTier("SOMETHING_ELSE")).toBe(false);
    // Enterprise sits ABOVE this tier and is not self-serve, so it is not the
    // top self-serve plan — its action is "contact sales", not "compare".
    expect(isTopSelfServeTier("ENTERPRISE")).toBe(false);
  });
});

describe("planHeroCta", () => {
  it("sends a Free or unknown account to the pricing page", () => {
    expect(planHeroCta("FREE")).toEqual({ label: "Choose a plan", target: "pricing" });
    // `null` is the state before the plan read answers; the card is a skeleton
    // then, so the safest reading is the one a brand-new account gets.
    expect(planHeroCta(null)).toEqual({ label: "Choose a plan", target: "pricing" });
    expect(planHeroCta("SOMETHING_ELSE")).toEqual({ label: "Choose a plan", target: "pricing" });
  });

  it("scrolls PRO to the section below, where its upgrade waits", () => {
    expect(planHeroCta("PRO")).toEqual({
      label: "Change plan",
      target: "change-plan-section",
    });
  });

  it("sends Pro Plus to the pricing page, because there is no section for it", () => {
    // The pairing that matters: this tier is exactly the one whose
    // `planChangeOptions` is empty, so the old "scroll to the section" branch
    // would have been a scroll to nothing. The label stays "Change plan"
    // because comparing plans is still what the customer is doing.
    expect(planChangeOptions("PRO_PLUS")).toEqual([]);
    expect(planHeroCta("PRO_PLUS")).toEqual({ label: "Change plan", target: "pricing" });
  });

  it("sends Enterprise to support instead of a self-serve action", () => {
    expect(planHeroCta("ENTERPRISE")).toEqual({ label: "Contact us", target: "support" });
  });
});

describe("planChangeExplainer", () => {
  it("describes only the upgrade when an upgrade is all that is offered", () => {
    // The PRO case, and the one the copy must not overpromise: the card renders
    // a single "Upgrade to Pro Plus" button, so a sentence about downgrades
    // would be explaining a control that is not on screen.
    const text = planChangeExplainer(planChangeOptions("PRO"));
    expect(text).toContain("Upgrades take effect immediately and are prorated");
    expect(text).toContain("carry over to the new plan");
    expect(text).not.toContain("Downgrades");
  });

  it("describes a downgrade when one is offered", () => {
    const text = planChangeExplainer([
      { tier: "PRO", label: "Pro", direction: "downgrade" },
    ]);
    expect(text).toContain("Downgrades start at the end of your current billing period");
    expect(text).not.toContain("Upgrades");
  });

  it("states both when both are offered", () => {
    const text = planChangeExplainer([
      { tier: "PRO_PLUS", label: "Pro Plus", direction: "upgrade" },
      { tier: "PRO", label: "Pro", direction: "downgrade" },
    ]);
    expect(text).toContain("Upgrades take effect immediately");
    expect(text).toContain("Downgrades start at the end");
    // Upgrade first, downgrade second — the order the buttons render in.
    expect(text.indexOf("Upgrades")).toBeLessThan(text.indexOf("Downgrades"));
  });

  it("says nothing when there is nothing to explain", () => {
    // Pro Plus and Enterprise reach no options at all, so an empty string is the
    // correct explanation for an empty card.
    expect(planChangeExplainer([])).toBe("");
  });
});

describe("describePlanChange", () => {
  it("surfaces an upgrade's carryover and its expiry from the response", () => {
    const carries = describePlanChange(
      result({
        carryover_credits: 320,
        carryover_expires_at: "2026-11-01T12:00:00Z",
        plan_credits: 2000,
      }),
    );

    expect(carries.kind).toBe("upgrade");
    expect(carries.title).toBe("You're now on Pro Plus");
    // Uses the response's own number, not a hardcoded one.
    expect(carries.detail).toContain("320 unspent credits carried over");
    // And the instant, formatted locally — never a raw ISO string.
    const expiry = formatDateOrNull("2026-11-01T12:00:00Z");
    expect(expiry).not.toBeNull();
    expect(carries.detail).toContain(expiry as string);
    expect(carries.detail).not.toContain("2026-11-01T12:00:00Z");
  });

  it("pluralises a single carried credit", () => {
    const carries = describePlanChange(
      result({ carryover_credits: 1, carryover_expires_at: "2026-11-01T12:00:00Z" }),
    );
    expect(carries.detail).toContain("1 unspent credit carried over");
    expect(carries.detail).not.toContain("credits carried");
    // Subject-verb agreement: a single credit "expires".
    expect(carries.detail).toContain("expires on");
    expect(carries.detail).not.toContain("expire on");
  });

  it("stays true when the API sent a carryover without an expiry", () => {
    const carries = describePlanChange(result({ carryover_credits: 12 }));
    expect(carries.detail).toContain("12 unspent credits carried over");
    // No invented date.
    expect(carries.detail).not.toContain("expire on");
  });

  it("states the new allowance when nothing carried over", () => {
    const upgraded = describePlanChange(result({ plan_credits: 2000 }));
    expect(upgraded.detail).toBe("Your new plan includes 2000 credits.");
  });

  it("falls back to the API's message when it reports no numbers", () => {
    const upgraded = describePlanChange(result({ message: "Upgrade applied." }));
    expect(upgraded.detail).toBe("Upgrade applied.");
  });

  it("reports a scheduled downgrade with the date it takes effect", () => {
    const scheduled = describePlanChange(
      result({
        tier: "PRO",
        previous_tier: "PRO_PLUS",
        scheduled_effective_at: "2026-12-15T12:00:00Z",
      }),
    );

    expect(scheduled.kind).toBe("downgrade");
    expect(scheduled.title).toBe("Downgrade to Pro scheduled");
    expect(scheduled.detail).toContain("You'll stay on Pro Plus until");
    expect(scheduled.detail).toContain(formatDateOrNull("2026-12-15T12:00:00Z") as string);
  });

  it("does not invent a date for a downgrade without one", () => {
    const scheduled = describePlanChange(
      result({ tier: "PRO", previous_tier: "PRO_PLUS", scheduled_effective_at: null }),
    );
    expect(scheduled.detail).toContain("until the end of your current billing period");
  });
});

describe("planChangeFailure", () => {
  it("sends a 409 (no subscription) to the pricing page", () => {
    const failure = planChangeFailure(409, "No active paid subscription to change.");
    expect(failure.goToPricing).toBe(true);
    expect(failure.message).toContain("Pick a plan");
  });

  it("keeps the server's wording for a 400", () => {
    const failure = planChangeFailure(400, "Already subscribed to this plan");
    expect(failure.goToPricing).toBe(false);
    expect(failure.message).toBe("Already subscribed to this plan");
  });

  it("supplies copy when a 400 or an unknown status carries no message", () => {
    expect(planChangeFailure(400, "").message).toBe(
      "That plan change isn't available for your account.",
    );
    expect(planChangeFailure(500, "").message).toBe(
      "Could not change your plan. Please try again.",
    );
    expect(planChangeFailure(502, "Stripe subscription could not be changed").message).toBe(
      "Stripe subscription could not be changed",
    );
  });
});
