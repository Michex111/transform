import { Badge, Card, Skeleton } from "@/components/ui";
import type { CreditPricingResponse } from "@/api/types";
import { bestValueIndex, creditPackCta, formatCount } from "@/lib/pricingPlans";

/**
 * Top-up credit packs. The "Best value" pick is the lowest price per credit as
 * decided by `lib/pricingPlans`; this component only renders it.
 */
export function CreditPacks({
  packs,
  loading,
  isAuthenticated,
  busyCredits,
  onBuy,
}: {
  packs: CreditPricingResponse[];
  loading: boolean;
  isAuthenticated: boolean;
  /** The pack amount currently being purchased, or `null`. */
  busyCredits: number | null;
  onBuy: (credits: number) => void;
}) {
  if (!loading && packs.length === 0) return null;

  const best = bestValueIndex(packs);
  const cta = creditPackCta({ isAuthenticated });

  return (
    <section id="credits" aria-labelledby="credits-heading" className="mt-16 scroll-mt-24">
      <Card className="p-6 sm:p-8">
        <h2 id="credits-heading" className="font-display text-2xl font-semibold">
          Credit packs
        </h2>
        <p className="mt-2 max-w-2xl text-sm text-muted">
          Need more than your plan includes? Top up any time. Purchased credits never
          expire; your plan credits refresh each month.
        </p>

        {loading ? (
          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-32 w-full" />
            ))}
          </div>
        ) : (
          <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {packs.map((pack, index) => {
              const busy = busyCredits === pack.credits;
              return (
                <li key={pack.credits}>
                  <button
                    type="button"
                    onClick={() => onBuy(pack.credits)}
                    disabled={busyCredits !== null}
                    className="flex h-full w-full flex-col items-start rounded-xl border border-outline bg-surface p-4 text-left transition-colors hover:border-primary/50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="flex w-full items-start justify-between gap-2">
                      <span className="font-display text-lg font-semibold">
                        {formatCount(pack.credits)} credits
                      </span>
                      {index === best && <Badge color="var(--color-success)">Best value</Badge>}
                    </span>
                    <span className="mt-2 font-display text-2xl font-semibold">
                      ${pack.price_usd.toFixed(2)}
                    </span>
                    <span className="mt-0.5 text-xs text-muted">
                      {busy
                        ? "Redirecting…"
                        : `$${pack.price_per_credit.toFixed(2)} per credit`}
                    </span>
                    <span className="mt-3 text-sm font-semibold text-primary">
                      {cta.label}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </section>
  );
}
