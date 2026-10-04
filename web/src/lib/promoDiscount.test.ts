/**
 * The copy a customer sees about their promotion code.
 *
 * The failure this guards against is not a crash but a lie: a student who reads
 * "$0.00" and assumes a charge, or who is not told that a free first month
 * renews at full price. Every assertion here is one of those statements.
 */

import { describe, expect, it } from "vitest";
import { discountSummary, formatMoney } from "./promoDiscount";

describe("formatMoney", () => {
  it("turns minor units into the currency's own format", () => {
    expect(formatMoney(999, "usd")).toBe("$9.99");
    expect(formatMoney(0, "usd")).toBe("$0.00");
  });

  it("respects zero-decimal currencies", () => {
    // The minor-unit exponent is not always 2; deriving it from Intl is what
    // keeps JPY from rendering as ¥1.00.
    expect(formatMoney(100, "jpy")).toBe("¥100");
  });

  it("falls back to the ISO code for a currency Intl rejects", () => {
    // Never the string "undefined": a bad currency should still be readable.
    expect(formatMoney(999, "not-a-currency")).toBe("NOT-A-CURRENCY 9.99");
  });
});

describe("discountSummary", () => {
  it("renders nothing when the API sent no discount", () => {
    // The crucial negative: "no data" must not produce an empty or a
    // fabricated discount row.
    expect(discountSummary(null)).toBeNull();
    expect(discountSummary(undefined)).toBeNull();
    expect(discountSummary({})).toBeNull();
    expect(discountSummary({ amount_total: 999, currency: "usd" })).toBeNull();
    expect(discountSummary({ discount_code: "  ", discount_percent_off: null })).toBeNull();
  });

  it("states plainly that nothing is due for a 100%-off 'once' code", () => {
    const summary = discountSummary({
      amount_total: 0,
      currency: "usd",
      discount_code: "CAMPUS2026",
      discount_percent_off: 100,
      discount_duration: "once",
    });

    expect(summary).not.toBeNull();
    expect(summary?.code).toBe("CAMPUS2026");
    expect(summary?.free).toBe(true);
    // Not "$0.00" on its own: an explicit free statement is what the customer
    // must read.
    expect(summary?.total).toBeNull();
    expect(summary?.headline).toBe("Free — first month");
    expect(summary?.dueToday).toBe("Nothing is due today.");
  });

  it("discloses the renewal after a free first month", () => {
    const summary = discountSummary(
      {
        amount_total: 0,
        currency: "usd",
        discount_code: "CAMPUS2026",
        discount_percent_off: 100,
        discount_duration: "once",
      },
      { renewalPrice: "$12.00" },
    );

    expect(summary?.detail).toContain("renews at the regular price of $12.00");
    expect(summary?.detail).toContain("free month");
  });

  it("says 'forever' for a permanent code and adds no renewal detail", () => {
    const summary = discountSummary({
      amount_total: 0,
      currency: "usd",
      discount_code: "STAFF",
      discount_percent_off: 100,
      discount_duration: "forever",
    });

    expect(summary?.headline).toBe("Free — forever");
    // Nothing happens after "forever", so there is nothing to disclose.
    expect(summary?.detail).toBeNull();
  });

  it("shows the reduced total from Stripe for a partial discount", () => {
    const summary = discountSummary({
      amount_total: 799,
      currency: "usd",
      discount_code: "CAMPUS20",
      discount_percent_off: 20,
      discount_duration: "repeating",
    });

    expect(summary?.free).toBe(false);
    expect(summary?.headline).toBe("20% off");
    expect(summary?.percentLabel).toBe("20% off");
    expect(summary?.total).toBe("$7.99");
    expect(summary?.dueToday).toBeNull();
  });

  it("does not claim a total when a partial code arrives without one", () => {
    const summary = discountSummary({
      discount_code: "CAMPUS20",
      discount_percent_off: 20,
    });

    expect(summary?.headline).toBe("20% off");
    expect(summary?.total).toBeNull();
    expect(summary?.free).toBe(false);
  });

  it("keeps a fractional percentage honest rather than rounding it", () => {
    // Stripe's percent_off can be fractional; rounding 12.5 to 13 would
    // misstate the discount.
    const summary = discountSummary({
      amount_total: 875,
      currency: "usd",
      discount_code: "PARTIAL",
      discount_percent_off: 12.5,
    });

    expect(summary?.percentLabel).toBe("12.5% off");
    expect(summary?.headline).toBe("12.5% off");
  });

  it("mentions a limited period for a repeating 100%-off code", () => {
    const summary = discountSummary({
      amount_total: 0,
      currency: "usd",
      discount_code: "TRIAL3",
      discount_percent_off: 100,
      discount_duration: "repeating",
    });

    expect(summary?.headline).toBe("Free for a limited time");
    expect(summary?.detail).toContain("limited number of months");
  });

  it("keeps the customer's casing on the code", () => {
    const summary = discountSummary({ discount_code: "Campus2026", discount_percent_off: 100 });
    expect(summary?.code).toBe("Campus2026");
  });
});
