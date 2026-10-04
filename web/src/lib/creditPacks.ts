/**
 * The credit packs offered on the Billing page.
 *
 * The pricing endpoint reports a per-pack `price_per_credit` as well as the
 * headline price. Which pack is the best value is therefore a decision the API
 * already made and we only have to *identify* — doing the arithmetic in the
 * component would eventually disagree with the number the endpoint sent.
 *
 * Deliberately pure and free of React: this repo's test environment is Node (no
 * jsdom), so the "which pack is the best value, and what does each label say"
 * decision lives here and is unit-tested, while the component only maps packs
 * to markup.
 */

import type { CreditPricingResponse } from "@/api/types";

/**
 * A USD amount as it is written in copy: `10` → `"$10"`, `9.99` → `"$9.99"`,
 * `0.1` → `"$0.10"`.
 *
 * Integral values drop the cents so a plan price reads as "$10/month" rather
 * than "$10.00/month" — the same shape the pricing page already renders. A
 * non-finite value returns `""` so a caller can omit the label instead of
 * printing "$NaN".
 */
export function formatUsd(value: number): string {
  if (!Number.isFinite(value)) return "";
  return Number.isInteger(value) ? `$${value}` : `$${value.toFixed(2)}`;
}

/** A positive money label, or `null` when the API did not quote a usable one. */
function moneyLabel(value: number): string | null {
  if (!Number.isFinite(value) || value <= 0) return null;
  return formatUsd(value);
}

export interface CreditPack {
  credits: number;
  priceUsd: number;
  pricePerCredit: number;
  /** `"$40"`, or `null` when the API did not quote a price. */
  priceLabel: string | null;
  /** `"$0.08 per credit"`, or `null` when the API did not quote a rate. */
  perCreditLabel: string | null;
  /**
   * True for exactly one pack — the cheapest per credit. `false` for every pack
   * when no pack sent a usable rate, rather than marking an arbitrary one.
   */
  bestValue: boolean;
}

/**
 * The index of the best-value pack, or `-1` when none can be compared.
 *
 * Lowest `price_per_credit` wins; a tie goes to the pack with more credits,
 * since at the same rate the bigger pack is never worse. The comparison is on
 * the API's own `price_per_credit` field, never on `price_usd / credits`.
 */
function bestValueIndex(packs: readonly CreditPack[]): number {
  let best = -1;
  for (let i = 0; i < packs.length; i += 1) {
    const pack = packs[i];
    if (!Number.isFinite(pack.pricePerCredit) || pack.pricePerCredit <= 0) continue;
    if (best === -1) {
      best = i;
      continue;
    }
    const current = packs[best];
    if (pack.pricePerCredit < current.pricePerCredit) best = i;
    else if (pack.pricePerCredit === current.pricePerCredit && pack.credits > current.credits) {
      best = i;
    }
  }
  return best;
}

/**
 * The packs as selectable cards, cheapest first, with the best value marked.
 *
 * Sorted by credit count so the row reads as a ladder regardless of the order
 * the endpoint happens to use (Array#sort is stable, so equal sizes keep the
 * API's order). A pack the API did not describe with a positive credit count is
 * dropped — rendering a "0 credits" card would be worse than omitting it.
 */
export function creditPacks(pricing: readonly CreditPricingResponse[]): CreditPack[] {
  const packs: CreditPack[] = pricing
    .filter((p) => Number.isFinite(p.credits) && p.credits > 0)
    .slice()
    .sort((a, b) => a.credits - b.credits)
    .map((p) => {
      const perCredit = moneyLabel(p.price_per_credit);
      return {
        credits: p.credits,
        priceUsd: p.price_usd,
        pricePerCredit: p.price_per_credit,
        priceLabel: moneyLabel(p.price_usd),
        perCreditLabel: perCredit ? `${perCredit} per credit` : null,
        bestValue: false,
      };
    });

  const best = bestValueIndex(packs);
  if (best !== -1) packs[best] = { ...packs[best], bestValue: true };
  return packs;
}

/**
 * The pack to select when the section first renders.
 *
 * The best-value pack when one exists, otherwise the largest (the last after
 * sorting) — never `null` for a non-empty list, so the buy button always has
 * something to act on. Returns `null` only when there is nothing to buy.
 */
export function defaultPack(packs: readonly CreditPack[]): CreditPack | null {
  if (packs.length === 0) return null;
  return packs.find((p) => p.bestValue) ?? packs[packs.length - 1];
}
