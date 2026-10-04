import { Link } from "react-router-dom";
import { Check } from "@phosphor-icons/react";
import { Button, Card } from "@/components/ui";
import type { SubscriptionPlanResponse } from "@/api/types";

/**
 * The closing Enterprise band. What it includes is read from the plan's own
 * payload, so nothing is claimed beyond what the API reports.
 */
export function EnterpriseBand({ plan }: { plan: SubscriptionPlanResponse | null }) {
  if (!plan) return null;

  return (
    <section aria-labelledby="enterprise-heading" className="mt-16">
      <Card className="grid gap-8 p-6 sm:p-8 lg:grid-cols-2">
        <div>
          <h2 id="enterprise-heading" className="font-display text-2xl font-semibold">
            {plan.name}
          </h2>
          <p className="mt-2 text-sm text-muted">
            Custom pricing and limits for teams that need more than the self-serve
            plans.
          </p>
          <ul className="mt-5 space-y-2">
            {plan.features.map((feature) => (
              <li key={feature} className="flex items-start gap-2 text-sm text-on-background">
                <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-success" aria-hidden />
                {feature}
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-col justify-center rounded-xl border border-outline bg-surface-variant/40 p-6">
          <h3 className="font-display text-lg font-semibold">Talk to sales</h3>
          <p className="mt-1.5 text-sm text-muted">
            Tell us what you need and we will put together a plan.
          </p>
          <Link to="/app/support" className="mt-5 block">
            <Button className="w-full">Contact sales</Button>
          </Link>
        </div>
      </Card>
    </section>
  );
}
