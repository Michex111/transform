import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Coins } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card } from "@/components/ui";
import { formatDate } from "@/lib/format";
import type {
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditTransactionResponse,
  SubscriptionStatusResponse,
} from "@/api/types";

export function BillingPage() {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const [plan, setPlan] = useState<SubscriptionStatusResponse | null>(null);
  const [credit, setCredit] = useState<CreditBalanceResponse | null>(null);
  const [pricing, setPricing] = useState<CreditPricingResponse[]>([]);
  const [history, setHistory] = useState<CreditTransactionResponse[]>([]);

  useEffect(() => {
    let active = true;
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
      .catch((e: Error) => error(e.message));
    return () => {
      active = false;
    };
  }, [client, error]);

  async function buy(amount: number) {
    try {
      await client.purchaseCredits(amount);
      const balance = await client.creditBalance();
      setCredit(balance);
      success(`${amount} credits added`);
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not buy credits");
    }
  }

  async function cancel() {
    if (!window.confirm("Cancel your subscription? You'll keep your tier until the period ends.")) return;
    try {
      await client.cancelSubscription();
      const p = await client.subscriptionStatus();
      setPlan(p);
      success("Subscription cancelled");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not cancel subscription");
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="font-display text-2xl font-semibold">Billing</h1>

      {/* Current plan */}
      <Card hover className="p-6">
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
            <Link to="/pricing">
              <Button variant="secondary">Manage subscription</Button>
            </Link>
            <Button variant="destructive" onClick={cancel}>
              Cancel subscription
            </Button>
          </div>
        </div>
      </Card>

      {/* Credits */}
      <Card hover className="p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Credits remaining</p>
            <p className="mt-1 font-display text-3xl font-semibold">{credit?.balance ?? 0}</p>
          </div>
          <Coins size={28} className="text-primary" />
        </div>
        <div className="mt-6">
          <p className="mb-2 text-sm font-medium">Buy credits</p>
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
        </div>
      </Card>

      {/* Transaction history */}
      <Card className="overflow-hidden">
        <div className="border-b border-outline px-5 py-4">
          <h2 className="font-display text-lg font-semibold">Transactions</h2>
        </div>
        {history.length === 0 ? (
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
    </div>
  );
}
