/**
 * The credit-wallet split shown on the Billing page.
 *
 * The balance endpoint reports three distinct populations that behave very
 * differently — the monthly plan bucket (refreshed every period), expiring
 * carryover (created by an upgrade, lost when it lapses) and purchased packs
 * (kept until spent). Folding them into one number hides the only part the user
 * can lose, which is exactly why the carryover exists as a separate bucket.
 *
 * Deliberately pure and free of React: the repo's test environment is Node (no
 * jsdom), so the "which rows, what do they say" decision lives here and is
 * unit-tested, while the component only maps rows to markup.
 */

import { formatDateOrNull } from "@/lib/format";
import type { CreditBalanceResponse } from "@/api/types";

export type CreditWalletBucket = "plan" | "carryover" | "purchased";

export interface CreditWalletRow {
  bucket: CreditWalletBucket;
  label: string;
  credits: number;
  /**
   * A second line for the row, e.g. the carryover's expiry, or `null` when
   * there is nothing true to add. Never a placeholder.
   */
  note: string | null;
}

/**
 * Whether the API sent any part of the wallet split.
 *
 * An older API omits the whole block; the three fields are all absent, and the
 * page must show only the headline total it showed before rather than a split
 * of nothing. This is deliberately "any" rather than "all": a partially
 * populated response is a newer API with a bucket at zero, not an old one.
 */
export function hasWalletSplit(
  balance: CreditBalanceResponse | null | undefined,
): boolean {
  if (!balance) return false;
  return (
    balance.plan_remaining != null ||
    balance.carryover_credits != null ||
    balance.purchased_credits != null
  );
}

/**
 * The number of credits the account actually holds.
 *
 * Prefers the server's `total_available` (which already excludes an expired
 * carryover) and falls back to `balance` — the plan bucket — for an older API
 * that has only that field. Never sums the buckets in the browser: expiry is a
 * server-side rule the client must not re-derive.
 */
export function availableCredits(
  balance: CreditBalanceResponse | null | undefined,
): number {
  if (!balance) return 0;
  return balance.total_available ?? balance.balance;
}

function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * The wallet split as rows, one per bucket that actually holds credits.
 *
 * A bucket is omitted when the API did not send it (`null`/absent) or it is
 * zero — the two are different reasons for the same silence, and neither should
 * render as "0 credits". An API that sends no split at all yields `[]`.
 */
export function creditWalletRows(
  balance: CreditBalanceResponse | null | undefined,
): CreditWalletRow[] {
  if (!hasWalletSplit(balance) || !balance) return [];

  const rows: CreditWalletRow[] = [];

  const plan = positive(balance.plan_remaining);
  if (plan !== null) {
    rows.push({ bucket: "plan", label: "Plan credits", credits: plan, note: null });
  }

  const carryover = positive(balance.carryover_credits);
  if (carryover !== null) {
    // The expiry is the whole reason this row matters: it is the credits that
    // can be lost. Formatted as a local instant (a reset/expiry is midnight UTC,
    // which is the previous evening west of UTC).
    const expiry = formatDateOrNull(balance.carryover_expires_at);
    rows.push({
      bucket: "carryover",
      label: "Carried over",
      credits: carryover,
      note: expiry ? `Expires ${expiry}` : null,
    });
  }

  const purchased = positive(balance.purchased_credits);
  if (purchased !== null) {
    rows.push({ bucket: "purchased", label: "Purchased", credits: purchased, note: null });
  }

  return rows;
}

export interface CreditSpendingOrderCopy {
  /** Whether the account is set to spend purchased credits before plan credits. */
  checked: boolean;
  /** True when the API actually reported the value (false on an older API). */
  known: boolean;
  /**
   * The honest explanation under the control.
   *
   * There is deliberately no success path here: no endpoint accepts this
   * preference yet, and this repo does not ship confirm-then-error or
   * fake-success UI. The control is rendered disabled with this note instead.
   */
  note: string;
}

/**
 * The read-only state and copy for the `purchased_credits_first` control.
 *
 * `checked` is only meaningful when `known` is true; an API that never sent the
 * field leaves the control off with a note that says so, rather than claiming
 * the account's real preference.
 */
export function creditSpendingOrderCopy(
  value: boolean | null | undefined,
): CreditSpendingOrderCopy {
  const known = typeof value === "boolean";
  return {
    checked: value === true,
    known,
    note: known
      ? "You can't change this here yet — our API doesn't accept the update. Ask support and we'll set it for you."
      : "We couldn't read this setting from the API, and there's no way to change it here yet. Ask support and we'll set it for you.",
  };
}

