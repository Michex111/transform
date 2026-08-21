import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button } from "@/components/ui";
import { Stagger, Item, Reveal } from "@/lib/motion";
import type { SubscriptionPlanResponse } from "@/api/types";

export function PricingPage() {
  const [plans, setPlans] = useState<SubscriptionPlanResponse[] | null>(null);
  const { error } = useToast();

  useEffect(() => {
    let active = true;
    api
      .subscriptionPlans()
      .then((p) => active && setPlans(p))
      .catch((e: Error) => error(e.message));
    return () => {
      active = false;
    };
  }, [error]);

  const fallback: SubscriptionPlanResponse[] = [
    {
      tier: "FREE",
      name: "Free",
      price_monthly_usd: null,
      storage_gb: 5,
      monthly_credits: 50,
      features: ["5 GB storage", "50 conversions/month", "10 API calls/month", "Community support"],
    },
    {
      tier: "PRO",
      name: "Pro",
      price_monthly_usd: 9.99,
      storage_gb: 50,
      monthly_credits: 500,
      features: ["50 GB storage", "500 conversions/month", "100 API calls/month", "Priority support"],
    },
    {
      tier: "PRO_PLUS",
      name: "Pro Plus",
      price_monthly_usd: 24.99,
      storage_gb: 100,
      monthly_credits: 2000,
      features: ["100 GB storage", "2000 conversions/month", "1000 API calls/month", "Priority processing", "24/7 support"],
    },
    {
      tier: "ENTERPRISE",
      name: "Enterprise",
      price_monthly_usd: null,
      storage_gb: 1000,
      monthly_credits: null,
      features: ["Custom storage", "Unlimited conversions", "Unlimited API access", "Dedicated support", "SLA guarantee", "Custom integrations"],
    },
  ];

  const list = plans ?? fallback;

  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <Reveal className="mb-12 text-center">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Pricing</p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">Simple plans. Real power.</h1>
        <p className="mt-3 text-muted">Start free. Upgrade when the work demands it.</p>
      </Reveal>

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
                <Link to={enterprise ? "/app/support" : "/register"} className="mt-6 block">
                  <Button
                    variant={popular ? "primary" : "secondary"}
                    className="w-full"
                  >
                    {enterprise
                      ? "Contact sales"
                      : plan.price_monthly_usd == null
                        ? "Start free"
                        : `Upgrade to ${plan.name}`}
                  </Button>
                </Link>
              </div>
            </Item>
          );
        })}
      </Stagger>
    </div>
  );
}
