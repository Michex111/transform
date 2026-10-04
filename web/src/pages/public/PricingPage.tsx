import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Skeleton } from "@/components/ui";
import { ComparisonTable } from "@/components/pricing/ComparisonTable";
import { CreditPacks } from "@/components/pricing/CreditPacks";
import { EnterpriseBand } from "@/components/pricing/EnterpriseBand";
import { PlanCard } from "@/components/pricing/PlanCard";
import { PricingFaq } from "@/components/pricing/PricingFaq";
import { PricingHero } from "@/components/pricing/PricingHero";
import type { CreditPricingResponse, SubscriptionPlanResponse } from "@/api/types";
import { trustedExternalUrl } from "@/lib/download";
import { Stagger, Item } from "@/lib/motion";
import { describePlanChange, planChangeFailure, tierRank } from "@/lib/planChange";
import { planCta } from "@/lib/pricingPlans";
import { embeddedCheckoutEnabled } from "@/lib/stripeCheckout";

export function PricingPage() {
  const { api: client, isAuthenticated, isLoading: authLoading } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();

  const [plans, setPlans] = useState<SubscriptionPlanResponse[]>([]);
  const [plansLoading, setPlansLoading] = useState(true);
  const [packs, setPacks] = useState<CreditPricingResponse[]>([]);
  const [packsLoading, setPacksLoading] = useState(true);
  /** The signed-in account's tier; `null` for a guest or while unknown. */
  const [currentTier, setCurrentTier] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  /** Which request is in flight, e.g. `checkout:PRO` or `credits:500`. */
  const [busy, setBusy] = useState<string | null>(null);

  // Catalogue data. Both reads are independent, so a failure of one still lets
  // the other render instead of blanking the whole page.
  useEffect(() => {
    let active = true;
    setPlansLoading(true);
    setPacksLoading(true);

    client
      .subscriptionPlans()
      .then((next) => active && setPlans(next))
      .catch((err: Error) => active && error(err.message))
      .finally(() => active && setPlansLoading(false));

    client
      .creditPricing()
      .then((next) => active && setPacks(next))
      .catch((err: Error) => active && error(err.message))
      .finally(() => active && setPacksLoading(false));

    return () => {
      active = false;
    };
  }, [client, error]);

  // The viewer's tier decides each card's CTA, so waiting for it avoids
  // flashing "Current plan" at a paid account while an older API answers.
  useEffect(() => {
    if (authLoading) return;
    if (!isAuthenticated) {
      setCurrentTier(null);
      setStatusLoading(false);
      return;
    }

    let active = true;
    setStatusLoading(true);
    client
      .subscriptionStatus()
      .then((status) => active && setCurrentTier(status.tier))
      .catch(() => active && setCurrentTier(null))
      .finally(() => active && setStatusLoading(false));

    return () => {
      active = false;
    };
  }, [client, isAuthenticated, authLoading]);

  const pageLoading = plansLoading || authLoading || (isAuthenticated && statusLoading);
  const enterprisePlan = plans.find((plan) => tierRank(plan.tier) === 3) ?? null;
  const busyCredits =
    busy?.startsWith("credits:") === true ? Number(busy.slice("credits:".length)) : null;
  const busyForPlan = (tier: string) =>
    busy === `checkout:${tier}` || busy === `change:${tier}`;

  /**
   * Start a new subscription. Embedded builds keep the payment form in the app;
   * everything else falls through to the hosted session, which is what every
   * deployment without a publishable key already used.
   */
  async function startCheckout(tier: string) {
    if (embeddedCheckoutEnabled()) {
      navigate(`/app/checkout?tier=${encodeURIComponent(tier)}`);
      return;
    }

    setBusy(`checkout:${tier}`);
    try {
      const { checkout_url } = await client.checkout(tier);
      // Guard the navigation the same way downloads are guarded: an
      // API-supplied `javascript:`/`data:` URL assigned to `location` would run
      // in this origin, and an arbitrary host would be an open redirect.
      const target = trustedExternalUrl(checkout_url);
      if (!target) throw new Error("Could not start checkout");
      window.location.assign(target);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start checkout");
      setBusy(null);
    }
  }

  /** Move an existing paid subscription to another tier (never a new checkout). */
  async function changePlan(tier: string) {
    setBusy(`change:${tier}`);
    try {
      const result = await client.changePlan(tier);
      const feedback = describePlanChange(result);
      success(`${feedback.title} — ${feedback.detail}`);
      // A successful write clears the client's read cache, so this is a live
      // read of the tier the account is now actually on.
      const status = await client.subscriptionStatus().catch(() => null);
      if (status) setCurrentTier(status.tier);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      const message = err instanceof Error ? err.message : "";
      const failure = planChangeFailure(status, message);
      if (failure.goToPricing) {
        // 409: there is no subscription to change yet, so start one instead of
        // leaving the user on a dead button with nothing to do next.
        await startCheckout(tier);
      } else {
        error(failure.message);
      }
    } finally {
      setBusy(null);
    }
  }

  async function buyCredits(credits: number) {
    if (!isAuthenticated) {
      navigate("/register");
      return;
    }

    if (embeddedCheckoutEnabled()) {
      navigate(`/app/checkout?credits=${encodeURIComponent(String(credits))}`);
      return;
    }

    setBusy(`credits:${credits}`);
    try {
      const { checkout_url } = await client.purchaseCredits(credits);
      const target = trustedExternalUrl(checkout_url);
      if (!target) throw new Error("Could not start checkout");
      window.location.assign(target);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start checkout");
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
      <PricingHero />

      <section aria-label="Plans">
        {pageLoading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="relative flex h-full flex-col rounded-2xl border border-outline bg-surface p-6"
              >
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
            {plans.map((plan) => (
              <Item key={plan.tier} className="h-full">
                <PlanCard
                  plan={plan}
                  cta={planCta({ plan, currentTier, isAuthenticated })}
                  busy={busyForPlan(plan.tier)}
                  onCheckout={() => startCheckout(plan.tier)}
                  onChangePlan={() => changePlan(plan.tier)}
                />
              </Item>
            ))}
          </Stagger>
        )}
      </section>

      {!pageLoading && <ComparisonTable plans={plans} />}

      {!authLoading && (
        <CreditPacks
          packs={packs}
          loading={packsLoading}
          isAuthenticated={isAuthenticated}
          busyCredits={busyCredits}
          onBuy={buyCredits}
        />
      )}

      <PricingFaq />

      {!pageLoading && <EnterpriseBand plan={enterprisePlan} />}
    </div>
  );
}
