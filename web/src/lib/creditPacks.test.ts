/**
 * The credit packs offered on the Billing page.
 *
 * The point of these rules is that the "Best value" badge is derived from the
 * API's own `price_per_credit` and not from a hardcoded pack size, and that a
 * pack the API could not price is labelled honestly (or omitted) rather than
 * rendered as "$0".
 */

import { describe, expect, it } from "vitest";
import type { CreditPricingResponse } from "@/api/types";
import { creditPacks, defaultPack, formatUsd } from "./creditPacks";

function pack(overrides: Partial<CreditPricingResponse> = {}): CreditPricingResponse {
  return { credits: 100, price_usd: 10, price_per_credit: 0.1, ...overrides };
}

describe("formatUsd", () => {
  it("drops the cents on whole dollars and keeps them otherwise", () => {
    expect(formatUsd(10)).toBe("$10");
    expect(formatUsd(9.99)).toBe("$9.99");
    expect(formatUsd(0.1)).toBe("$0.10");
    expect(formatUsd(300)).toBe("$300");
  });

  it("returns an empty string for a non-finite value so callers omit it", () => {
    expect(formatUsd(Number.NaN)).toBe("");
    expect(formatUsd(Number.POSITIVE_INFINITY)).toBe("");
  });
});

describe("creditPacks", () => {
  it("sorts cheapest first and marks the lowest per-credit rate", () => {
    // Deliberately out of order, the way a hand-written fixture often is.
    const packs = creditPacks([
      pack({ credits: 1000, price_usd: 70, price_per_credit: 0.07 }),
      pack({ credits: 100, price_usd: 10, price_per_credit: 0.1 }),
      pack({ credits: 5000, price_usd: 300, price_per_credit: 0.06 }),
      pack({ credits: 500, price_usd: 40, price_per_credit: 0.08 }),
    ]);

    expect(packs.map((p) => p.credits)).toEqual([100, 500, 1000, 5000]);
    expect(packs.filter((p) => p.bestValue).map((p) => p.credits)).toEqual([5000]);
    expect(packs[3].perCreditLabel).toBe("$0.06 per credit");
    expect(packs[0].priceLabel).toBe("$10");
  });

  it("breaks a per-credit tie toward the larger pack", () => {
    const packs = creditPacks([
      pack({ credits: 100, price_per_credit: 0.05 }),
      pack({ credits: 1000, price_per_credit: 0.05 }),
    ]);
    expect(packs.find((p) => p.bestValue)?.credits).toBe(1000);
  });

  it("marks nothing when no pack sent a usable rate", () => {
    const packs = creditPacks([
      pack({ credits: 100, price_usd: 0, price_per_credit: 0 }),
      pack({ credits: 500, price_usd: Number.NaN, price_per_credit: Number.NaN }),
    ]);
    expect(packs.every((p) => !p.bestValue)).toBe(true);
    expect(packs[0].priceLabel).toBeNull();
    expect(packs[1].perCreditLabel).toBeNull();
    expect(packs[1].priceLabel).toBeNull();
  });

  it("drops packs with no usable credit count", () => {
    expect(creditPacks([pack({ credits: 0 }), pack({ credits: 250 })])).toHaveLength(1);
  });

  it("is empty for an API that returned nothing", () => {
    expect(creditPacks([])).toEqual([]);
  });
});

describe("defaultPack", () => {
  it("prefers the best value pack", () => {
    const packs = creditPacks([
      pack({ credits: 100, price_per_credit: 0.1 }),
      pack({ credits: 1000, price_per_credit: 0.06 }),
    ]);
    expect(defaultPack(packs)?.credits).toBe(1000);
  });

  it("falls back to the largest pack when no rate is available", () => {
    const packs = creditPacks([
      pack({ credits: 100, price_per_credit: 0 }),
      pack({ credits: 1000, price_per_credit: 0 }),
    ]);
    expect(defaultPack(packs)?.credits).toBe(1000);
  });

  it("is null when there is nothing to buy", () => {
    expect(defaultPack([])).toBeNull();
  });
});
