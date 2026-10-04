import { Link } from "react-router-dom";
import { Check, Sparkle } from "@phosphor-icons/react";
import { Button } from "@/components/ui";
import type { SubscriptionPlanResponse } from "@/api/types";
import { aiPlanFeatures } from "@/lib/planFeatures";
import { planStats, type PlanCta } from "@/lib/pricingPlans";

/**
 * One plan card. Purely presentational: every decision (the CTA, the stat row)
 * arrives precomputed from `lib/pricingPlans`, and the page owns the requests.
 */
export function PlanCard({
  plan,
  cta,
  busy,
  onCheckout,
  onChangePlan,
}: {
  plan: SubscriptionPlanResponse;
  cta: PlanCta;
  /** A request for this card is in flight. */
  busy: boolean;
  onCheckout: () => void;
  onChangePlan: () => void;
}) {
  const popular = plan.tier === "PRO_PLUS";
  const enterprise = cta.kind === "contact";
  const variant = popular ? "primary" : "secondary";
  const stats = planStats(plan);
  const aiRows = aiPlanFeatures(plan.ai);

  function action() {
    if (cta.kind === "register") {
      return (
        <Link to="/register" className="block">
          <Button variant={variant} className="w-full">
            {cta.label}
          </Button>
        </Link>
      );
    }
    if (cta.kind === "contact") {
      return (
        <Link to="/app/support" className="block">
          <Button variant="secondary" className="w-full">
            {cta.label}
          </Button>
        </Link>
      );
    }
    if (cta.kind === "current") {
      return (
        <Button variant="secondary" className="w-full" disabled>
          {cta.label}
        </Button>
      );
    }
    return (
      <Button
        variant={variant}
        className="w-full"
        disabled={busy}
        onClick={cta.kind === "checkout" ? onCheckout : onChangePlan}
      >
        {busy
          ? cta.kind === "checkout"
            ? "Redirecting…"
            : "Changing…"
          : cta.label}
      </Button>
    );
  }

  return (
    <div
      className={`relative flex h-full flex-col rounded-2xl border bg-surface p-6 transition-all ${
        popular ? "border-primary shadow-lg" : "border-outline"
      } hover:-translate-y-1 hover:border-primary/50`}
    >
      {popular && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-primary px-3 py-0.5 text-xs font-semibold text-on-primary">
          Most popular
        </span>
      )}

      <h2 className="font-display text-lg font-semibold">{plan.name}</h2>
      <p className="mt-3 font-display text-3xl font-semibold">
        {enterprise
          ? "Custom"
          : plan.price_monthly_usd == null
            ? "$0"
            : `$${plan.price_monthly_usd}`}
        {!enterprise && <span className="text-base font-normal text-muted">/mo</span>}
      </p>

      {/* Structured facts from the payload; a stat the API did not send is
          omitted rather than rendered as a placeholder. */}
      <dl className="mt-4 flex flex-wrap gap-x-4 gap-y-2 border-y border-outline py-3">
        {stats.map((stat) => (
          <div key={stat.label} className="min-w-[4.5rem] flex-1">
            <dt className="text-[11px] font-medium uppercase tracking-wide text-muted">
              {stat.label}
            </dt>
            <dd className="mt-0.5 text-sm font-semibold text-on-background">
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>

      <ul className="mt-6 flex-1 space-y-2.5">
        {plan.features.map((feature) => (
          <li key={feature} className="flex items-start gap-2 text-sm text-on-background">
            <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-success" aria-hidden />
            {feature}
          </li>
        ))}
      </ul>

      {/* A distinct group for the assistant, only when the plan has one.
          `aiPlanFeatures` owns the wording and returns `[]` for no assistant,
          so nothing hardcoded leaks in here. */}
      {aiRows.length > 0 && (
        <div className="mt-5 rounded-xl border border-outline bg-surface-variant/40 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
            <Sparkle size={13} weight="fill" className="shrink-0 text-primary" aria-hidden />
            AI assistant
          </p>
          <ul className="mt-2.5 space-y-2">
            {aiRows.map((row) => (
              <li key={row} className="flex items-start gap-2 text-sm text-on-background">
                <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-primary" aria-hidden />
                {row}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6">{action()}</div>
    </div>
  );
}
