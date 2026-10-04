/**
 * The usage meters on the Billing page.
 *
 * Both meters come from two different endpoints that report limits in different
 * ways, and both have a "no limit to show" state (a tier with no persistent
 * credits, or an API that did not send storage stats). The rule shared by the
 * two is: omit the meter rather than draw it against a fabricated limit.
 *
 * Deliberately pure and free of React: this repo's test environment is Node (no
 * jsdom), so "which meters exist, and what do they read" is decided and tested
 * here, while the component only draws the bars.
 */

import { availableCredits } from "@/lib/creditWallet";
import { formatBytes, formatDateOrNull } from "@/lib/format";
import type { CreditBalanceResponse, StorageStats } from "@/api/types";

export interface UsageMeter {
  key: "credits" | "storage";
  label: string;
  used: number;
  limit: number;
  /** Completed percentage, clamped to 0..100. */
  percent: number;
  usedLabel: string;
  limitLabel: string;
  /** Accessible name for the bar — several live on one page. */
  ariaLabel: string;
  /** A second line under the bar, or `null` when there is nothing true to add. */
  note: string | null;
}

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/**
 * The monthly plan credits, or `null` when there is no allowance to measure.
 *
 * "Used" is `allowance - available`, clamped at zero: an account holding
 * purchased credits can have more available than the monthly allowance, and a
 * negative "used" would read as a bug. The total deliberately comes from
 * `availableCredits` (the server's own `total_available`), so an expired
 * carryover is already excluded rather than re-derived here.
 */
export function creditUsageMeter(
  balance: CreditBalanceResponse | null | undefined,
): UsageMeter | null {
  if (!balance) return null;
  const allowance = balance.monthly_allowance;
  if (typeof allowance !== "number" || !Number.isFinite(allowance) || allowance <= 0) return null;

  const available = Math.max(0, availableCredits(balance));
  const used = Math.max(0, Math.min(allowance, allowance - available));
  const reset = formatDateOrNull(balance.credits_reset_at);

  return {
    key: "credits",
    label: "Conversions this month",
    used,
    limit: allowance,
    percent: clampPercent((used / allowance) * 100),
    usedLabel: `${used}`,
    limitLabel: `${allowance}`,
    ariaLabel: "Conversions used this billing period",
    note: reset ? `Resets ${reset}` : null,
  };
}

/**
 * Storage usage, or `null` when the API did not report a usable quota.
 *
 * The percentage is the server's own `used_percent` when it is a real number;
 * otherwise it is derived from the two byte counts, which is not an invention —
 * it is the same division the server performs. A zero or missing limit means
 * there is no quota to draw a bar against, so the meter is omitted.
 */
export function storageUsageMeter(stats: StorageStats | null | undefined): UsageMeter | null {
  if (!stats) return null;
  const { used_bytes: used, limit_bytes: limit } = stats;
  if (!Number.isFinite(limit) || limit <= 0) return null;
  if (!Number.isFinite(used) || used < 0) return null;

  const percent = Number.isFinite(stats.used_percent)
    ? clampPercent(stats.used_percent)
    : clampPercent((used / limit) * 100);

  return {
    key: "storage",
    label: "Storage",
    used,
    limit,
    percent,
    usedLabel: formatBytes(used),
    limitLabel: formatBytes(limit),
    ariaLabel: "Storage used",
    note: null,
  };
}

/** The meters to render, skipping either one the API could not describe. */
export function usageMeters(input: {
  balance: CreditBalanceResponse | null | undefined;
  storage: StorageStats | null | undefined;
}): UsageMeter[] {
  return [creditUsageMeter(input.balance), storageUsageMeter(input.storage)].filter(
    (meter): meter is UsageMeter => meter !== null,
  );
}
