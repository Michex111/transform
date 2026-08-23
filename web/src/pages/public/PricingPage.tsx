import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Skeleton } from "@/components/ui";
import { Stagger, Item, Reveal } from "@/lib/motion";
import type { SubscriptionPlanResponse } from "@/api/types";

export function PricingPage() {
  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const { error } = useToast();
  const { isAuthenticated } = useAuth();

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .subscriptionPlans()
      .then((p) => active && setPlans(p))
      .catch((e: Error) => error(e.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [error]);

  async function upgrade(tier: string) {
    setCheckoutLoading(tier);
    try {
      const { checkout_url } = await api.checkout(tier);
      window.location.assign(checkout_url);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start checkout");
      setCheckoutLoading(null);
    }
  }

  const list = plans;

  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Reveal className="mb-12 text-center">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Pricing</p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">Simple plans. Real power.</h1>
        <p className="mt-3 text-muted">Start free. Upgrade when the work demands it.</p>
      </Reveal>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="relative flex h-full flex-col rounded-2xl border border-outline bg-surface p-6">
              <Skeleton className="h-5 w-24" />
              <Skeleton className="mt-3 h-8 w-20" />
              <div className="mt-6 flex-1 space-y-2.5">
                {Array.from({ length: 4 }).map((_, j) => (
                  <Skeleton key={j} className="h-3 w-full" />
                ))}
              </div>
              <Skeleton className="mt-6 h-10 w-full" />
            </div>
          ))}
        </div>
      ) : (
      <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {list.map((plan) => {
          const popular = plan.tier === "PRO_PLUS";
          const enterprise = plan.tier === "ENTERPRISE";
          return (
            <Item key={plan.tier} className="h-full">
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
                  {plan.price_monthly_usd == null ? "Custom" : `$${plan.price_monthly_usd}`}
                  {plan.price_monthly_usd != null && (
                    <span className="text-base font-normal text-muted">/mo</span>
                  )}
                </p>
                <ul className="mt-6 flex-1 space-y-2.5">
                  {plan.features.map((f) => (
                    <li key={f} className="flex items-start gap-2 text-sm text-on-background">
                      <Check size={16} weight="bold" className="mt-0.5 shrink-0 text-success" />
                      {f}
                    </li>
                  ))}
                </ul>
                <div className="mt-6">
                  {enterprise ? (
                    <Link to="/app/support" className="block">
                      <Button variant="secondary" className="w-full">
                        Contact sales
                      </Button>
                    </Link>
                  ) : plan.price_monthly_usd == null ? (
                    <Link to="/register" className="block">
                      <Button variant="secondary" className="w-full">
                        Start free
                      </Button>
                    </Link>
                  ) : isAuthenticated ? (
                    <Button
                      variant={popular ? "primary" : "secondary"}
                      className="w-full"
                      disabled={checkoutLoading === plan.tier}
                      onClick={() => upgrade(plan.tier)}
                    >
                      {checkoutLoading === plan.tier ? "Redirecting…" : `Upgrade to ${plan.name}`}
                    </Button>
                  ) : (
                    <Link to="/register" className="block">
                      <Button variant={popular ? "primary" : "secondary"} className="w-full">
                        {`Upgrade to ${plan.name}`}
                      </Button>
                    </Link>
                  )}
                </div>
              </div>
            </Item>
          );
        })}
      </Stagger>
      )}
    </div>
  );
}
