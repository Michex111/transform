/**
 * The credit-wallet split.
 *
 * The rules under test are the ones that decide whether a user ever learns that
 * part of their balance can expire: which buckets get a row, when one is
 * omitted, and what the headline number is.
 */

import { describe, expect, it } from "vitest";
import { formatDateOrNull } from "@/lib/format";
import type { CreditBalanceResponse } from "@/api/types";
import {
  availableCredits,
  creditSpendingOrderCopy,
  creditWalletRows,
  hasWalletSplit,
} from "./creditWallet";

function balance(overrides: Partial<CreditBalanceResponse> = {}): CreditBalanceResponse {
  return {
    balance: 0,
    tier: "PRO",
    monthly_allowance: 500,
    monthly_remaining: 0,
    credits_reset_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

describe("hasWalletSplit", () => {
  it("is false before anything loads and for an API that sends no split", () => {
    expect(hasWalletSplit(null)).toBe(false);
    expect(hasWalletSplit(undefined)).toBe(false);
    // An older API: only `balance` and `monthly_*`.
    expect(hasWalletSplit(balance({ balance: 5 }))).toBe(false);
  });

  it("is true as soon as any bucket was reported — including an explicit zero", () => {
    // A reported zero is the API saying "this bucket is empty", which is
    // different from "this API does not know about buckets".
    expect(hasWalletSplit(balance({ plan_remaining: 0 }))).toBe(true);
    expect(hasWalletSplit(balance({ carryover_credits: 0 }))).toBe(true);
    expect(hasWalletSplit(balance({ purchased_credits: 40 }))).toBe(true);
  });
});

describe("availableCredits", () => {
  it("prefers the server's total, which already excludes expired carryover", () => {
    expect(
      availableCredits(balance({ balance: 100, plan_remaining: 100, total_available: 340 })),
    ).toBe(340);
  });

  it("falls back to the plan balance on an older API", () => {
    expect(availableCredits(balance({ balance: 100 }))).toBe(100);
  });

  it("is zero when nothing has loaded", () => {
    expect(availableCredits(null)).toBe(0);
  });
});

describe("creditWalletRows", () => {
  it("returns nothing for an API that sends no split", () => {
    expect(creditWalletRows(balance({ balance: 5 }))).toEqual([]);
    expect(creditWalletRows(null)).toEqual([]);
  });

  it("splits the three populations", () => {
    const rows = creditWalletRows(
      balance({
        plan_remaining: 120,
        carryover_credits: 320,
        carryover_expires_at: "2026-11-01T12:00:00Z",
        purchased_credits: 1000,
      }),
    );

    expect(rows.map((r) => r.bucket)).toEqual(["plan", "carryover", "purchased"]);
    expect(rows[0]).toEqual({
      bucket: "plan",
      label: "Plan credits",
      credits: 120,
      note: null,
    });
    expect(rows[1].label).toBe("Carried over");
    expect(rows[1].credits).toBe(320);
    // The expiry is the whole point of the carryover row, formatted locally.
    expect(rows[1].note).toBe(`Expires ${formatDateOrNull("2026-11-01T12:00:00Z")}`);
    expect(rows[2]).toEqual({
      bucket: "purchased",
      label: "Purchased",
      credits: 1000,
      note: null,
    });
  });

  it("omits a zero bucket instead of printing '0 credits'", () => {
    const rows = creditWalletRows(
      balance({ plan_remaining: 0, carryover_credits: 0, purchased_credits: 40 }),
    );
    expect(rows.map((r) => r.bucket)).toEqual(["purchased"]);
  });

  it("omits a bucket the API did not send", () => {
    // `plan_remaining` absent (older field set), carryover present.
    const rows = creditWalletRows(balance({ carryover_credits: 15 }));
    expect(rows.map((r) => r.bucket)).toEqual(["carryover"]);
  });

  it("keeps a carryover row even when the API sent no expiry", () => {
    const rows = creditWalletRows(
      balance({ carryover_credits: 15, carryover_expires_at: null }),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].credits).toBe(15);
    // No placeholder date.
    expect(rows[0].note).toBeNull();
  });

  it("drops a malformed expiry rather than printing a broken one", () => {
    const rows = creditWalletRows(
      balance({ carryover_credits: 15, carryover_expires_at: "not-a-date" }),
    );
    expect(rows[0].note).toBeNull();
  });

  it("ignores negative and non-finite counts", () => {
    const rows = creditWalletRows(
      balance({ plan_remaining: -5, carryover_credits: Number.NaN, purchased_credits: 3 }),
    );
    expect(rows.map((r) => r.bucket)).toEqual(["purchased"]);
  });

  it("returns nothing when every reported bucket is zero", () => {
    expect(
      creditWalletRows(balance({ plan_remaining: 0, carryover_credits: 0, purchased_credits: 0 })),
    ).toEqual([]);
  });
});

describe("creditSpendingOrderCopy", () => {
  it("reports a real preference as known and reflects it", () => {
    expect(creditSpendingOrderCopy(true)).toMatchObject({ checked: true, known: true });
    expect(creditSpendingOrderCopy(false)).toMatchObject({ checked: false, known: true });
  });

  it("marks a missing preference as unknown instead of claiming 'off'", () => {
    for (const value of [null, undefined]) {
      expect(creditSpendingOrderCopy(value)).toMatchObject({ checked: false, known: false });
    }
  });

  it("always explains that the setting cannot be changed here", () => {
    // No endpoint accepts this preference, so there is no success path to
    // The setting is savable now, so the note explains its SCOPE (API-only)
    // rather than apologising for a missing endpoint.
    expect(creditSpendingOrderCopy(true).note).toContain("API conversions only");
    expect(creditSpendingOrderCopy(null).note).toContain("couldn't read this setting");
  });
});
