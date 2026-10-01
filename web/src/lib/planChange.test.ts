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
  planChangeFailure,
  planChangeOptions,
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

  it("offers a downgrade from PRO_PLUS", () => {
    expect(planChangeOptions("PRO_PLUS")).toEqual([
      { tier: "PRO", label: "Pro", direction: "downgrade" },
    ]);
  });

  it("offers nothing to a Free account or an Enterprise one", () => {
    // Empty is the signal to omit the section rather than render dead buttons.
    expect(planChangeOptions("FREE")).toEqual([]);
    expect(planChangeOptions(null)).toEqual([]);
    expect(planChangeOptions("ENTERPRISE")).toEqual([]);
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
