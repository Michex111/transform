/**
 * The plan-and-usage hero on the Billing page.
 *
 * The one rule this card exists to enforce: the primary, repeated action is
 * changing the plan. Cancelling is *not* here at all — it lives in a quiet card
 * at the bottom of the page behind a retention flow. The hero only offers one
 * primary action ("Choose a plan" on Free, "Change plan" on a paid plan, "Contact
 * us" on Enterprise), a quiet payment-method jump and, when a cancellation is
 * already scheduled, "Resume plan".
 *
 * Where that primary button *leads* is `planHeroCta`'s decision, not this
 * file's: Free and Pro Plus go to the pricing page, PRO scrolls to the Change
 * plan section below, and Enterprise goes to support. Pro Plus is the case that
 * makes the distinction necessary — it has nothing above it, so that section is
 * not rendered for it and a scroll would be a dead control.
 *
 * Every value is optional on the wire: an older API that does not send the plan
 * price, `cancel_at_period_end` or the usage figures simply renders fewer lines
 * rather than a fabricated zero.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Badge, ProgressBar, Skeleton } from "@/components/ui";
import { tierLabel, planHeroCta } from "@/lib/planChange";
import { monthlyPriceLabel, periodLabel, planStatusTone, type PlanTone } from "@/lib/billingOverview";
import { usageMeters, type UsageMeter } from "@/lib/usageMeters";
import type {
  CreditBalanceResponse,
  StorageStats,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
} from "@/api/types";

const TONE_COLOR: Record<PlanTone, string> = {
  success: "var(--color-success)",
  warning: "var(--color-warning)",
  error: "var(--color-error)",
  muted: "var(--color-muted)",
};

function statusLabel(status: string | null | undefined): string {
  const value = (status ?? "").trim();
  if (!value) return "Active";
  return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ");
}

function Meter({ meter }: { meter: UsageMeter }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p className="min-w-0 truncate text-sm font-medium text-on-background">{meter.label}</p>
        <p className="shrink-0 font-mono text-xs text-muted">
          {meter.usedLabel} of {meter.limitLabel}
        </p>
      </div>
      <ProgressBar className="mt-2" value={meter.percent} ariaLabel={meter.ariaLabel} />
      {meter.note && <p className="mt-1.5 text-xs text-muted">{meter.note}</p>}
    </div>
  );
}

export interface PlanHeroCardProps {
  loading: boolean;
  plan: SubscriptionStatusResponse | null;
  credit: CreditBalanceResponse | null;
  storage: StorageStats | null;
  plans: SubscriptionPlanResponse[];
  onScrollToChangePlan: () => void;
  onScrollToPaymentMethod: () => void;
  onResume: () => void;
  resumeBusy: boolean;
}

export function PlanHeroCard({
  loading,
  plan,
  credit,
  storage,
  plans,
  onScrollToChangePlan,
  onScrollToPaymentMethod,
  onResume,
  resumeBusy,
}: PlanHeroCardProps) {
  const navigate = useNavigate();
  const meters = useMemo(() => usageMeters({ balance: credit, storage }), [credit, storage]);
  const price = monthlyPriceLabel(plans, plan?.tier);
  const period = periodLabel(plan);
  const ending = plan?.cancel_at_period_end === true;
  // The hero's primary action, decided in one testable place. It is deliberately
  // *not* re-derived here from `tierRank`/`planChangeOptions`: two call sites
  // deriving the same rule is how a button ends up scrolling to a section that
  // its own tier never renders.
  const cta = planHeroCta(plan?.tier);

  function handlePrimaryAction() {
    if (cta.target === "pricing") navigate("/pricing");
    else if (cta.target === "support") navigate("/app/support");
    else onScrollToChangePlan();
  }

  return (
    <Card className="p-6">
      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-3 w-32" />
          <div className="grid gap-5 pt-2 sm:grid-cols-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">Current plan</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {/* `tierLabel` turns the raw enum (`PRO_PLUS`) into "Pro Plus";
                    the API's tier values are identifiers, not copy. */}
                <h2 className="font-display text-2xl font-semibold">
                  {tierLabel(plan?.tier)}
                </h2>
                <Badge color={TONE_COLOR[planStatusTone(plan?.status)]}>
                  {statusLabel(plan?.status)}
                </Badge>
              </div>
              <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted">
                {price && <span>{price}</span>}
                {price && period && <span aria-hidden>·</span>}
                {period && <span>{period}</span>}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {/* A button rather than a link: `Button` renders a real `<button>`,
                  and nesting one inside an `<a>` is invalid markup and confuses
                  assistive tech about what the control is. The label and the
                  destination both come from `planHeroCta`, so this row cannot
                  offer a "Change plan" that scrolls to an absent section. */}
              <Button onClick={handlePrimaryAction}>{cta.label}</Button>
              <Button variant="ghost" size="sm" onClick={onScrollToPaymentMethod}>
                Manage payment method
              </Button>
              {ending && (
                <Button variant="secondary" onClick={onResume} disabled={resumeBusy}>
                  {resumeBusy ? "Resuming…" : "Resume plan"}
                </Button>
              )}
            </div>
          </div>

          {meters.length > 0 && (
            <div className="mt-6 grid gap-5 border-t border-outline pt-5 sm:grid-cols-2">
              {meters.map((meter) => (
                <Meter key={meter.key} meter={meter} />
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
