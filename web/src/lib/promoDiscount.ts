/**
 * What the customer is told about a promotion code the session applied.
 *
 * Pure and React-free, like every other decision in `lib/`: this repo's test
 * environment is Node with no jsdom, so the words that tell a student whether
 * they were charged have to come out of a function that can be tested directly.
 * The checkout page only renders what these helpers return.
 *
 * The two facts that must never be ambiguous:
 *
 *   - when `amount_total` is `0`, nothing is due today — say so in words rather
 *     than showing a price that looks like a charge; and
 *   - a `"once"` discount starts billing afterwards, which must be said without
 *     alarming the customer.
 */

import type { DiscountDuration } from "@/api/types";

/**
 * The promotion block of a checkout session, exactly as `normalizeCheckout`
 * leaves it. All fields optional so an API older than the feature is a valid
 * input that simply yields no discount UI.
 */
export interface DiscountFacts {
  amount_total?: number | null;
  currency?: string | null;
  discount_code?: string | null;
  discount_percent_off?: number | null;
  discount_duration?: DiscountDuration | null;
}

export interface DiscountSummary {
  /** The code Stripe actually applied, in the customer's own casing. */
  code: string | null;
  /** Pill copy, e.g. `"100% off"` or `"20% off"`; `null` when unknown. */
  percentLabel: string | null;
  /** The plain statement, e.g. `"Free — first month"` or `"20% off"`. */
  headline: string;
  /** True when `amount_total` is exactly `0`. */
  free: boolean;
  /** The reduced total in the session's own currency; `null` when free/unknown. */
  total: string | null;
  /** Copy for the amount line when nothing is due; `null` when something is. */
  dueToday: string | null;
  /** What happens when the discount ends; `null` when there is nothing to say. */
  detail: string | null;
}

/**
 * Format a minor-unit amount from the session: `999`, `"usd"` → `"$9.99"`.
 *
 * `Intl.NumberFormat` in the currency's own style, so the symbol and decimal
 * places come from the runtime's locale data instead of a hardcoded `$`. The
 * minor-unit exponent is derived from the very same formatter — which is what
 * makes zero-decimal currencies (`jpy` → `"¥100"`) come out right without a
 * hand-maintained table.
 */
export function formatMoney(amount: number, currency: string): string {
  if (!Number.isFinite(amount)) return "";
  const code = (currency || "usd").trim().toLowerCase();
  try {
    const formatter = new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code.toUpperCase(),
    });
    // `maximumFractionDigits` is the currency's minor-unit exponent for a
    // currency formatter (2 for USD, 0 for JPY, 3 for BHD).
    const exponent = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    return formatter.format(amount / 10 ** exponent);
  } catch {
    // An unrecognised currency code makes Intl throw. Fall back to the ISO code
    // so a bad value still renders as usable text, never as "undefined".
    const value = (amount / 100).toFixed(2);
    return `${code.toUpperCase()} ${value}`;
  }
}

/** A trimmed non-empty string, or `null`. `""` is "the API did not say". */
function text(raw: string | null | undefined): string | null {
  const value = (raw ?? "").trim();
  return value === "" ? null : value;
}

/**
 * Describe an applied discount, or `null` when there is nothing to describe.
 *
 * Returning `null` for a payload with no code and no percentage is the whole
 * point: "the API did not send a discount" must render no row, not an empty or
 * misleading one. `renewalPrice` is the plan's own formatted price (from the
 * pricing catalogue) and is only ever used to make the after-the-discount
 * disclosure concrete; nothing here recomputes a discount locally.
 */
export function discountSummary(
  facts: DiscountFacts | null | undefined,
  options: { renewalPrice?: string | null } = {},
): DiscountSummary | null {
  if (!facts) return null;

  const code = text(facts.discount_code);
  const percent =
    typeof facts.discount_percent_off === "number" &&
    Number.isFinite(facts.discount_percent_off)
      ? facts.discount_percent_off
      : null;
  const amountTotal =
    typeof facts.amount_total === "number" && Number.isFinite(facts.amount_total)
      ? facts.amount_total
      : null;

  // No evidence of a discount → render nothing at all.
  if (code === null && percent === null) return null;

  const free = amountTotal === 0;
  const duration = facts.discount_duration ?? null;
  // Stripe's own percentage, not a value re-derived from the total. Kept
  // fractional (e.g. 12.5%) rather than rounded, which would misstate it.
  const percentLabel = percent !== null ? `${formatPercent(percent)}% off` : null;
  // Prefer Stripe's own total over re-deriving one from the percentage.
  const total =
    amountTotal !== null && amountTotal > 0
      ? formatMoney(amountTotal, facts.currency ?? "usd")
      : null;

  return {
    code,
    percentLabel,
    headline: discountHeadline(percent, percentLabel, free, duration),
    free,
    total,
    dueToday: free ? "Nothing is due today." : null,
    detail: discountDetail(free, duration, text(options.renewalPrice)),
  };
}

/** A discount percentage with at most two decimals (`100` → `"100"`). */
function formatPercent(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

/** The plain statement of what the customer gets. */
function discountHeadline(
  percent: number | null,
  percentLabel: string | null,
  free: boolean,
  duration: DiscountDuration | null,
): string {
  if (percent === 100) {
    if (duration === "once") return "Free — first month";
    if (duration === "forever") return "Free — forever";
    if (duration === "repeating") return "Free for a limited time";
    return "Free";
  }
  if (free) return "Free";
  if (percentLabel !== null) return percentLabel;
  // A code applied but no percentage reported: say a discount landed without
  // inventing one.
  return "Discount applied";
}

/**
 * What happens after the discount, when the API told us enough to say.
 *
 * `"forever"` deliberately has no detail — there is nothing after it. An
 * absent/unknown duration has none either: inventing a renewal story is worse
 * than saying nothing.
 */
function discountDetail(
  free: boolean,
  duration: DiscountDuration | null,
  renewalPrice: string | null,
): string | null {
  const priceSuffix = renewalPrice ? ` of ${renewalPrice}` : "";

  if (duration === "once") {
    return free
      ? `After your free month, your plan renews at the regular price${priceSuffix}.`
      : `This discount applies to your first month only, then renews at the regular price${priceSuffix}.`;
  }
  if (duration === "repeating") {
    return free
      ? `Free for a limited number of months, then renews at the regular price${priceSuffix}.`
      : `This discount applies for a limited number of months, then renews at the regular price${priceSuffix}.`;
  }
  return null;
}
