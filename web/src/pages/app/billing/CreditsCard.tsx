/**
 * The credits card on the Billing page.
 *
 * Two things live here that used to be separate: the wallet (total plus the
 * plan/carryover/purchased split, so an expiring bucket is visible) and the
 * credit packs. Packs are selectable cards rather than bare buttons so the price
 * per credit — the thing that actually helps someone choose — can be shown, with
 * the API's cheapest rate marked as the best value.
 *
 * The purchase itself keeps the existing behaviour exactly: embedded checkout
 * when the build can render it, otherwise a guarded hosted redirect.
 */

import { useState } from "react";
import { Coins } from "@phosphor-icons/react";
import { Button, Card, Skeleton } from "@/components/ui";
import { availableCredits, creditWalletRows } from "@/lib/creditWallet";
import { creditPacks, defaultPack, type CreditPack } from "@/lib/creditPacks";
import { formatDateOrNull } from "@/lib/format";
import type { CreditBalanceResponse, CreditPricingResponse } from "@/api/types";

export interface CreditsCardProps {
  loading: boolean;
  credit: CreditBalanceResponse | null;
  pricing: CreditPricingResponse[];
  onBuy: (amount: number) => void;
}

export function CreditsCard({ loading, credit, pricing, onBuy }: CreditsCardProps) {
  const packs = creditPacks(pricing);
  const [selectedCredits, setSelectedCredits] = useState<number | null>(null);

  const creditsTotal = availableCredits(credit);
  const walletRows = creditWalletRows(credit);
  const reset = formatDateOrNull(credit?.credits_reset_at);

  const selected =
    packs.find((pack) => pack.credits === selectedCredits) ?? defaultPack(packs);

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">
            Credits remaining
          </p>
          {loading ? (
            <Skeleton className="mt-1 h-8 w-24" />
          ) : (
            <p className="mt-1 font-display text-3xl font-semibold">{creditsTotal}</p>
          )}
          {!loading && reset && (
            <p className="mt-1 text-xs text-muted">
              Plan credits reset <span className="font-medium text-on-background">{reset}</span>
            </p>
          )}
        </div>
        <Coins size={28} className="shrink-0 text-primary" aria-hidden />
      </div>

      {/* The split, so expiring carryover is visible. A bucket the API did not
          send, or one at zero, is omitted rather than printed as 0. */}
      {!loading && walletRows.length > 0 && (
        <dl className="mt-4 space-y-2 border-t border-outline pt-4">
          {walletRows.map((row) => (
            <div key={row.bucket} className="flex items-start justify-between gap-4">
              <dt className="min-w-0 text-sm text-muted">
                {row.label}
                {row.note && <span className="mt-0.5 block text-xs">{row.note}</span>}
              </dt>
              <dd className="shrink-0 font-mono text-sm font-semibold text-on-background">
                {row.credits}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {!loading && packs.length > 0 && (
        <div className="mt-6 border-t border-outline pt-5">
          <p className="text-sm font-medium">Buy credits</p>
          <p className="mt-1 text-xs text-muted">Purchased credits never expire.</p>

          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {packs.map((pack) => (
              <PackTile
                key={pack.credits}
                pack={pack}
                selected={selected?.credits === pack.credits}
                onSelect={() => setSelectedCredits(pack.credits)}
              />
            ))}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button onClick={() => selected && onBuy(selected.credits)} disabled={!selected}>
              {selected ? `Buy ${selected.credits} credits` : "Buy credits"}
            </Button>
            {selected?.priceLabel && (
              <span className="text-xs text-muted">{selected.priceLabel} one-time</span>
            )}
          </div>
        </div>
      )}

      {loading && (
        <div className="mt-6 border-t border-outline pt-5">
          <Skeleton className="h-3 w-20" />
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function PackTile({
  pack,
  selected,
  onSelect,
}: {
  pack: CreditPack;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${pack.credits} credits${pack.priceLabel ? ` for ${pack.priceLabel}` : ""}`}
      className={`flex min-w-0 flex-col items-start rounded-lg border px-3 py-2.5 text-left transition-colors ${
        selected
          ? "border-primary bg-primary/10"
          : "border-outline hover:border-outline-strong hover:bg-surface-variant/50"
      }`}
    >
      <span className="flex w-full flex-wrap items-center justify-between gap-1">
        <span className="font-semibold text-on-background">{pack.credits}</span>
        {pack.bestValue && (
          <span className="shrink-0 rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-success">
            Best value
          </span>
        )}
      </span>
      {pack.priceLabel && (
        <span className="mt-0.5 text-sm text-on-background">{pack.priceLabel}</span>
      )}
      {pack.perCreditLabel && (
        <span className="mt-0.5 font-mono text-[11px] text-muted">{pack.perCreditLabel}</span>
      )}
    </button>
  );
}
