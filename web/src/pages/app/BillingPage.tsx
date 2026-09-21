import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Coins } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton, SkeletonText } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { formatDate, formatDateOrNull } from "@/lib/format";
import type {
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditTransactionResponse,
  SubscriptionStatusResponse,
} from "@/api/types";

export function BillingPage() {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [plan, setPlan] = useState<SubscriptionStatusResponse | null>(null);
  const [credit, setCredit] = useState<CreditBalanceResponse | null>(null);
  const [pricing, setPricing] = useState<CreditPricingResponse[]>([]);
  const [history, setHistory] = useState<CreditTransactionResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [portalLoading, setPortalLoading] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);

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

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      client.subscriptionStatus(),
      client.creditBalance(),
      client.creditPricing(),
      client.creditHistory(),
    ])
      .then(([p, c, pr, h]) => {
        if (!active) return;
        setPlan(p);
        setCredit(c);
        setPricing(pr);
        setHistory(h);
      })
      .catch((e: Error) => error(e.message))
      .finally(() => active && setLoading(false));
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
        .then((balance) => setCredit(balance))
        .catch((e: Error) => error(e.message));
    };
    window.addEventListener("credits:updated", onCreditsUpdated);
    return () => window.removeEventListener("credits:updated", onCreditsUpdated);
  }, [client, error]);

  async function buy(amount: number) {
    try {
      // Credit packs go through Stripe-hosted Checkout. Credits are granted by
      // the backend only after the payment confirms (checkout webhook).
      const { checkout_url } = await client.purchaseCredits(amount);
      window.location.assign(checkout_url);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not start credit purchase");
    }
  }

  async function openPortal() {
    setPortalLoading(true);
    try {
      const { portal_url } = await client.createPortalSession();
      window.location.assign(portal_url);
    } catch (err) {
      // The portal requires an existing Stripe customer. A user with no paid
      // subscription (Free tier) has no customer yet, so guide them to upgrade
      // instead of showing a raw error.
      const message = err instanceof Error ? err.message : "";
      if (message.toLowerCase().includes("no stripe customer")) {
        navigate("/pricing");
      } else {
        error(message || "Could not open billing portal");
      }
    } finally {
      setPortalLoading(false);
    }
  }

  async function cancel() {
    if (cancelling) return;
    setCancelling(true);
    try {
      await client.cancelSubscription();
      const p = await client.subscriptionStatus();
      setPlan(p);
      success("Subscription cancelled");
      setCancelOpen(false);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not cancel subscription");
    } finally {
      setCancelling(false);
    }
  }

  // Absent on an older API and `null` for tiers without persistent credits —
  // both omit the line rather than showing a placeholder date.
  const resetLabel = formatDateOrNull(credit?.credits_reset_at);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="font-display text-2xl font-semibold">Billing</h1>

      {/* Current plan */}
      <Card hover className="p-6">
        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-8 w-40" />
            <Skeleton className="h-3 w-32" />
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">Current plan</p>
              <div className="mt-1 flex items-center gap-2">
                <h2 className="font-display text-2xl font-semibold">{plan?.tier ?? "Free"}</h2>
                <span className="rounded-full bg-success/15 px-2.5 py-0.5 text-xs font-semibold text-success">
                  {plan?.status ?? "Active"}
                </span>
              </div>
              {plan?.current_period_end && (
                <p className="mt-1 text-sm text-muted">Renews {formatDate(plan.current_period_end)}</p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="secondary" onClick={openPortal} disabled={portalLoading}>
                {portalLoading ? "Opening…" : "Manage subscription"}
              </Button>
              {plan && plan.tier !== "FREE" && (
                <Button variant="destructive" onClick={() => setCancelOpen(true)}>
                  Cancel subscription
                </Button>
              )}
            </div>
        </div>
        )}
      </Card>

      {/* Credits */}
      <Card hover className="p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Credits remaining</p>
            {loading ? (
              <Skeleton className="mt-1 h-8 w-24" />
            ) : (
              <p className="mt-1 font-display text-3xl font-semibold">{credit?.balance ?? 0}</p>
            )}
          </div>
          <Coins size={28} className="text-primary" />
        </div>
        {/* When the monthly plan allowance refreshes. Shown in the viewer's
            local timezone, and omitted for tiers without persistent credits. */}
        {!loading && resetLabel && (
          <p className="mt-2 text-xs text-muted">
            Resets <span className="font-medium text-on-background">{resetLabel}</span>
          </p>
        )}
        <div className="mt-6">
          <p className="mb-2 text-sm font-medium">Buy credits</p>
          {loading ? (
            <div className="flex flex-wrap gap-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-28" />
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {pricing.map((p) => (
                <button
                  key={p.credits}
                  onClick={() => buy(p.credits)}
                  className="rounded-lg border border-outline-strong px-4 py-2 text-sm hover:bg-surface-variant"
                >
                  <span className="font-semibold">{p.credits}</span>{" "}
                  <span className="text-muted">· ${p.price_usd}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* Transaction history */}
      <Card className="overflow-hidden">
        <div className="border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Transactions</h2>
        </div>
        {loading ? (
          <div className="space-y-3 px-5 py-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center justify-between gap-4">
                <SkeletonText lines={2} />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        ) : history.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-muted">No transactions yet.</p>
        ) : (
          <ul className="divide-y divide-outline">
            {history.map((t) => (
              <li key={t.id} className="grid grid-cols-[1fr_auto_auto] items-center gap-4 px-5 py-3">
                <div>
                  <p className="text-sm text-on-background capitalize">{t.transaction_type}</p>
                  <p className="font-mono text-xs text-muted">{t.reference_id}</p>
                </div>
                <span className="font-mono text-sm text-on-background">
                  {t.amount > 0 ? `+${t.amount}` : t.amount}
                </span>
                <span className="font-mono text-xs text-muted">{formatDate(t.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Cancel subscription confirmation */}
      <Modal
        open={cancelOpen}
        onClose={() => {
          if (!cancelling) setCancelOpen(false);
        }}
        title="Cancel subscription?"
        maxWidth="max-w-sm"
      >
        <p className="text-sm text-on-background">
          You'll keep your current tier until the period ends.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setCancelOpen(false)} disabled={cancelling}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={cancel} disabled={cancelling}>
            {cancelling ? "Cancelling…" : "Cancel subscription"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
