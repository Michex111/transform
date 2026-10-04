/**
 * Billing.
 *
 * Structured to keep customers rather than to make leaving easy. Reading top to
 * bottom the page goes: what you're on and how much you've used → the next plan
 * up → change plan (only where there is an upgrade to make) → buying credits →
 * managing the card → the billing record → and only then, in a deliberately
 * quiet card at the very bottom, cancelling. The cancel card opens a three-step
 * retention wizard instead of cancelling immediately, and nothing above it is a
 * cancel control.
 *
 * Every API field is optional-by-contract: the page is deployed independently of
 * the API, so a response that omits the plan price, the wallet split,
 * `cancel_at_period_end` or invoices renders fewer lines rather than a fabricated
 * zero. `null` from the API means "did not say", never `0`.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { CalendarX, Lifebuoy } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card } from "@/components/ui";
import { trustedExternalUrl } from "@/lib/download";
import { formatDateOrNull } from "@/lib/format";
import { planChangeOptions, tierRank } from "@/lib/planChange";
import { embeddedCheckoutEnabled } from "@/lib/stripeCheckout";
import { PlanHeroCard } from "./billing/PlanHeroCard";
import { UpgradeNudgeCard } from "./billing/UpgradeNudgeCard";
import { PlanChangeCard } from "./billing/PlanChangeCard";
import { CreditsCard } from "./billing/CreditsCard";
import { PaymentMethodSection } from "./billing/PaymentMethodSection";
import { BillingHistoryCard } from "./billing/BillingHistoryCard";
import { CancelPlanCard } from "./billing/CancelPlanCard";
import { CancelRetentionModal } from "./billing/CancelRetentionModal";
import type {
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditTransactionResponse,
  DashboardResponse,
  InvoiceListResponse,
  StorageStats,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
} from "@/api/types";

interface BillingData {
  plan: SubscriptionStatusResponse | null;
  credit: CreditBalanceResponse | null;
  pricing: CreditPricingResponse[];
  history: CreditTransactionResponse[];
  storage: StorageStats | null;
  plans: SubscriptionPlanResponse[];
  invoices: InvoiceListResponse | null;
  invoicesFailure: string | null;
}

const EMPTY: BillingData = {
  plan: null,
  credit: null,
  pricing: [],
  history: [],
  storage: null,
  plans: [],
  invoices: null,
  invoicesFailure: null,
};

/** A user-facing message for a rejected promise we did not type. */
function failureMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message;
  return "Something went wrong loading your billing details.";
}

export function BillingPage() {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const [data, setData] = useState<BillingData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);

  const changePlanRef = useRef<HTMLDivElement | null>(null);
  const paymentRef = useRef<HTMLDivElement | null>(null);

  // Show feedback when the user returns from Stripe-hosted Checkout or the
  // Customer Portal, then strip the query params so a refresh doesn't re-show it.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get("checkout") === "success") {
      success("Payment successful — your subscription is now active.");
    } else if (params.get("credits") === "success") {
      success("Payment successful — credits have been added.");
    } else if (params.get("checkout") === "cancelled") {
      error("Checkout was cancelled — no changes were made.");
    } else if (params.get("credits") === "cancelled") {
      error("Checkout was cancelled — no credits were added.");
    } else if (params.get("checkout") !== null || params.get("credits") !== null) {
      // Unknown/other params: just clean the URL.
    } else {
      return;
    }
    navigate("/app/billing", { replace: true });
  }, [location.search, navigate, success, error]);

  // Load everything the page shows. Each call settles independently: a failing
  // invoice read (or an API that predates the endpoint) must not blank out the
  // plan and wallet, and the invoice section has its own honest message for it.
  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.allSettled([
      client.subscriptionStatus(),
      client.creditBalance(),
      client.creditPricing(),
      client.creditHistory(),
      client.dashboard(),
      client.subscriptionPlans(),
      client.listInvoices(),
    ])
      .then(
        ([plan, credit, pricing, history, dashboard, plans, invoices]: [
          PromiseSettledResult<SubscriptionStatusResponse>,
          PromiseSettledResult<CreditBalanceResponse>,
          PromiseSettledResult<CreditPricingResponse[]>,
          PromiseSettledResult<CreditTransactionResponse[]>,
          PromiseSettledResult<DashboardResponse>,
          PromiseSettledResult<SubscriptionPlanResponse[]>,
          PromiseSettledResult<InvoiceListResponse>,
        ]) => {
          if (!active) return;
          setData({
            plan: plan.status === "fulfilled" ? plan.value : null,
            credit: credit.status === "fulfilled" ? credit.value : null,
            pricing: pricing.status === "fulfilled" ? pricing.value : [],
            history: history.status === "fulfilled" ? history.value : [],
            storage: dashboard.status === "fulfilled" ? dashboard.value.storage_stats : null,
            plans: plans.status === "fulfilled" ? plans.value : [],
            invoices: invoices.status === "fulfilled" ? invoices.value : null,
            invoicesFailure:
              invoices.status === "rejected" ? failureMessage(invoices.reason) : null,
          });
          // The plan and the wallet are the page; if either failed, say so.
          if (plan.status === "rejected") error(failureMessage(plan.reason));
          else if (credit.status === "rejected") error(failureMessage(credit.reason));
          setLoading(false);
        },
      );
    return () => {
      active = false;
    };
  }, [client, error]);

  // Refresh the credit balance live whenever a conversion completes and the
  // worker publishes the user's updated remaining credits via JobsContext.
  useEffect(() => {
    const onCreditsUpdated = () => {
      client
        .creditBalance()
        .then((balance) => setData((prev) => ({ ...prev, credit: balance })))
        .catch((e: Error) => error(e.message));
    };
    window.addEventListener("credits:updated", onCreditsUpdated);
    return () => window.removeEventListener("credits:updated", onCreditsUpdated);
  }, [client, error]);

  // Re-read the plan and wallet after a successful in-app plan change, so the
  // current-plan card, the credit split and the history all agree. Deliberately
  // does NOT set `loading`: flipping the whole page back to skeletons after a
  // one-click upgrade reads as a page reload. `changePlan` is a POST, so the
  // client's read cache was already dropped and these are live values.
  //
  // The storage stats come along because an upgrade changes the quota, and a
  // stale meter next to a freshly-changed plan is the kind of detail that makes
  // a page feel broken.
  const refreshBilling = useCallback(async () => {
    try {
      const [plan, credit, history, dashboard] = await Promise.all([
        client.subscriptionStatus(),
        client.creditBalance(),
        client.creditHistory(),
        client.dashboard(),
      ]);
      setData((prev) => ({
        ...prev,
        plan,
        credit,
        history,
        storage: dashboard.storage_stats,
      }));
    } catch (e) {
      error(e instanceof Error ? e.message : "Could not refresh your billing details");
    }
  }, [client, error]);

  async function buy(amount: number) {
    // The branded in-app checkout handles this when the build can render it;
    // the hosted redirect below is the fallback (see `lib/stripeCheckout`).
    if (embeddedCheckoutEnabled()) {
      navigate(`/app/checkout?credits=${amount}`);
      return;
    }
    try {
      // Credit packs go through Stripe-hosted Checkout. Credits are granted by
      // the backend only after the payment confirms (checkout webhook).
      const { checkout_url } = await client.purchaseCredits(amount);
      // Never assign an API-supplied URL straight to `location`: a `javascript:`
      // value would execute in this origin. `trustedExternalUrl` returns the URL
      // only when its scheme/host passes the same allowlist downloads use.
      const target = trustedExternalUrl(checkout_url);
      if (!target) throw new Error("The checkout link was not valid. Please try again.");
      window.location.assign(target);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start credit purchase");
    }
  }

  async function resume() {
    if (resumeBusy) return;
    setResumeBusy(true);
    try {
      await client.resumeSubscription();
      success("Your plan will renew again");
      await refreshBilling();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not resume your subscription");
    } finally {
      setResumeBusy(false);
    }
  }

  function scrollTo(ref: React.RefObject<HTMLDivElement | null>) {
    ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const plan = data.plan;
  const free = tierRank(plan?.tier) === 0;
  const hasSubscription = plan != null && !free;
  const ending = plan?.cancel_at_period_end === true;
  const endsOn = formatDateOrNull(plan?.current_period_end);
  const showCancelCard = !loading && hasSubscription && !ending;
  // Whether the Change plan card has anything to offer this tier. Gated on the
  // wrapper (not just inside the card) because the page is a `space-y-6` stack:
  // an empty wrapper would still add a 24px gap where the section used to be.
  // Pro Plus is the tier this is about — nothing above it, and its downgrade is
  // offered on the pricing page the hero's button now points at.
  const showPlanChange = !loading && planChangeOptions(plan?.tier ?? null).length > 0;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold">Billing</h1>
          <p className="mt-1 text-sm text-muted">
            Manage your plan, credits and payment details.
          </p>
        </div>
        {/* Quiet by design: support is a first-class route, not a billing action. */}
        <Link
          to="/app/support"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition-colors hover:text-on-background"
        >
          <Lifebuoy size={15} aria-hidden />
          Need help?
        </Link>
      </div>

      {/* A scheduled cancellation is the one thing that outranks the plan card:
          it changes what the plan card means. Prominent, but calm. */}
      {!loading && ending && (
        <Card className="border-warning/40 bg-warning/5 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <CalendarX size={20} className="mt-0.5 shrink-0 text-warning" aria-hidden />
              <div className="min-w-0">
                <p className="text-sm font-medium text-on-background">
                  {endsOn
                    ? `Your plan ends on ${endsOn}. Nothing is charged after that.`
                    : "Your plan ends at the end of the current billing period. Nothing is charged after that."}
                </p>
                <p className="mt-0.5 text-sm text-muted">You can keep it and renew as normal.</p>
              </div>
            </div>
            <Button variant="secondary" size="sm" onClick={resume} disabled={resumeBusy}>
              {resumeBusy ? "Resuming…" : "Resume plan"}
            </Button>
          </div>
        </Card>
      )}

      {/* 2 — Plan & usage. The primary action here is change/choose, never cancel. */}
      <PlanHeroCard
        loading={loading}
        plan={plan}
        credit={data.credit}
        storage={data.storage}
        plans={data.plans}
        onScrollToChangePlan={() => scrollTo(changePlanRef)}
        onScrollToPaymentMethod={() => scrollTo(paymentRef)}
        onResume={resume}
        resumeBusy={resumeBusy}
      />

      {/* 3 — Upgrade nudge. Omitted entirely when the plan list failed to load. */}
      {!loading && data.plans.length > 0 && (
        <UpgradeNudgeCard
          plans={data.plans}
          currentTier={plan?.tier ?? null}
          onChanged={() => void refreshBilling()}
        />
      )}

      {/* 4 — Change plan, the scroll target for the hero's primary button when
          that button is a scroll at all. Omitted for Free, Enterprise and Pro
          Plus, which is why the hero asks `planHeroCta` before scrolling. */}
      {showPlanChange && (
        <div ref={changePlanRef} id="change-plan" className="scroll-mt-6">
          <PlanChangeCard
            currentTier={plan?.tier ?? null}
            onChanged={() => void refreshBilling()}
          />
        </div>
      )}

      {/* 5 — Credits: the wallet, then the packs. */}
      <CreditsCard
        loading={loading}
        credit={data.credit}
        pricing={data.pricing}
        onBuy={(amount) => void buy(amount)}
      />

      {/* 6 — Payment method. Quiet and late; its own logic is untouched. */}
      {!loading && (
        <div ref={paymentRef} id="payment-method" className="scroll-mt-6">
          <PaymentMethodSection hasSubscription={hasSubscription} />
        </div>
      )}

      {/* 7 — Billing history: invoices first, credit activity behind a disclosure. */}
      <BillingHistoryCard
        loading={loading}
        invoices={data.invoices}
        invoicesFailure={data.invoicesFailure}
        history={data.history}
      />

      {/* 8 — Cancel, deliberately the last and quietest thing on the page. */}
      {showCancelCard && <CancelPlanCard onOpen={() => setCancelOpen(true)} />}

      {/* 9 — The retention wizard. */}
      <CancelRetentionModal
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        plan={plan}
        credit={data.credit}
        plans={data.plans}
        onChanged={() => void refreshBilling()}
      />
    </div>
  );
}
