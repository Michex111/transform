/**
 * The usage meters on the Billing page.
 *
 * The rules under test are the ones that keep a meter honest: no bar without a
 * real limit, no negative "used" for an account holding purchased credits, and a
 * percentage that cannot leave 0..100 even if the API sends something odd.
 */

import { describe, expect, it } from "vitest";
import type { CreditBalanceResponse, StorageStats } from "@/api/types";
import { creditUsageMeter, storageUsageMeter, usageMeters } from "./usageMeters";

function balance(overrides: Partial<CreditBalanceResponse> = {}): CreditBalanceResponse {
  return {
    balance: 300,
    tier: "PRO",
    monthly_allowance: 500,
    monthly_remaining: 300,
    credits_reset_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

function storage(overrides: Partial<StorageStats> = {}): StorageStats {
  return {
    used_bytes: 1024 ** 3,
    limit_bytes: 50 * 1024 ** 3,
    used_percent: 2,
    file_count: 10,
    ...overrides,
  };
}

describe("creditUsageMeter", () => {
  it("measures used against the allowance and reports the reset", () => {
    const meter = creditUsageMeter(balance());
    expect(meter?.used).toBe(200);
    expect(meter?.limit).toBe(500);
    expect(meter?.percent).toBe(40);
    expect(meter?.usedLabel).toBe("200");
    expect(meter?.limitLabel).toBe("500");
    expect(meter?.note).toMatch(/^Resets /);
    expect(meter?.ariaLabel).toBe("Conversions used this billing period");
  });

  it("clamps 'used' at zero when purchased credits exceed the allowance", () => {
    const meter = creditUsageMeter(balance({ balance: 400, total_available: 900 }));
    expect(meter?.used).toBe(0);
    expect(meter?.percent).toBe(0);
  });

  it("clamps the percentage to 100", () => {
    const meter = creditUsageMeter(balance({ monthly_remaining: 0, total_available: 0 }));
    expect(meter?.used).toBe(500);
    expect(meter?.percent).toBe(100);
  });

  it("is null for a tier with no allowance or an older API", () => {
    expect(creditUsageMeter(balance({ monthly_allowance: null }))).toBeNull();
    expect(creditUsageMeter(balance({ monthly_allowance: 0 }))).toBeNull();
    expect(creditUsageMeter(null)).toBeNull();
  });

  it("omits the reset note when the date is missing", () => {
    expect(creditUsageMeter(balance({ credits_reset_at: null }))?.note).toBeNull();
  });
});

describe("storageUsageMeter", () => {
  it("uses the server's own percentage", () => {
    const meter = storageUsageMeter(storage({ used_percent: 12.5 }));
    expect(meter?.percent).toBe(12.5);
    expect(meter?.usedLabel).toBe("1.0 GB");
    expect(meter?.limitLabel).toBe("50 GB");
    expect(meter?.ariaLabel).toBe("Storage used");
  });

  it("derives the percentage when the API did not send one", () => {
    const meter = storageUsageMeter(storage({ used_percent: Number.NaN, used_bytes: 25, limit_bytes: 50 }));
    expect(meter?.percent).toBe(50);
  });

  it("is null without a real quota", () => {
    expect(storageUsageMeter(storage({ limit_bytes: 0 }))).toBeNull();
    expect(storageUsageMeter(null)).toBeNull();
  });
});

describe("usageMeters", () => {
  it("returns both when both are measurable", () => {
    expect(usageMeters({ balance: balance(), storage: storage() }).map((m) => m.key)).toEqual([
      "credits",
      "storage",
    ]);
  });

  it("skips whichever the API could not describe", () => {
    expect(usageMeters({ balance: null, storage: storage() }).map((m) => m.key)).toEqual(["storage"]);
    expect(usageMeters({ balance: balance(), storage: null }).map((m) => m.key)).toEqual(["credits"]);
    expect(usageMeters({ balance: null, storage: null })).toEqual([]);
  });
});
