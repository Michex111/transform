/**
 * The "next plan up" nudge on the Billing page.
 *
 * This is the retention counterweight to the cancel card: it always shows the
 * customer the immediately-next plan and what that plan's own payload says it
 * includes. The gains are never invented — an older API that omits the
 * structured fields produces a shorter list, and if the plans endpoint failed
 * the whole card is omitted rather than rendered empty.
 *
 * For a Free account the CTA is the pricing page (a subscription has to be
 * started through checkout); for a paid account it is an in-place `changePlan`
 * with the same feedback the change-plan card gives.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowUp } from "@phosphor-icons/react";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card } from "@/components/ui";
import { describePlanChange, planChangeFailure, tierLabel, tierRank } from "@/lib/planChange";
import { nextPlanAbove, upgradeGains } from "@/lib/billingOverview";
import type { SubscriptionPlanResponse } from "@/api/types";

export interface UpgradeNudgeCardProps {
  plans: SubscriptionPlanResponse[];
  currentTier: string | null;
  /** Re-reads the page data after a successful plan change. */
  onChanged: () => void;
}

export function UpgradeNudgeCard({ plans, currentTier, onChanged }: UpgradeNudgeCardProps) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<{ title: string; detail: string } | null>(null);

  // Nothing to compare against if the plan list never loaded.
  const upgrade = nextPlanAbove(plans, currentTier);
  if (!upgrade) return null;

  const target = upgrade.plan;
  const label = tierLabel(target.tier);

  // The highest self-serve tier has no self-serve plan above it: Enterprise is
  // a conversation, so it gets one restrained line instead of a gains list.
  if (tierRank(currentTier) === 2 && !upgrade.selfServe) {
    return (
      <Card className="p-6">
        <h2 className="font-display text-lg font-semibold">Need more than {label}?</h2>
        <p className="mt-1 text-sm text-muted">
          Enterprise covers teams that need custom limits, a service-level agreement or a dedicated
          point of contact.
        </p>
        <div className="mt-4">
          <Link
            to="/app/support"
            className="text-sm font-semibold text-primary underline-offset-4 hover:underline"
          >
            Talk to us about Enterprise
          </Link>
        </div>
      </Card>
    );
  }

  const gains = upgradeGains(target);

  async function change() {
    if (busy) return;
    setBusy(true);
    setConfirmation(null);
    try {
      const result = await client.changePlan(target.tier);
      const feedback = describePlanChange(result);
      setConfirmation({ title: feedback.title, detail: feedback.detail });
      success(`${feedback.title} — ${feedback.detail}`);
      onChanged();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      const message = err instanceof Error ? err.message : "";
      const failure = planChangeFailure(status, message);
      error(failure.message);
      if (failure.goToPricing) navigate("/pricing");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card hover className="p-6">
      <h2 className="font-display text-lg font-semibold">
        {upgrade.selfServe ? `Get more with ${label}` : `Start with ${label}`}
      </h2>
      <p className="mt-1 text-sm text-muted">
        {upgrade.selfServe
          ? "Upgrade takes effect right away, and unspent credits carry over."
          : "Everything below is included when you subscribe."}
      </p>

      {gains.length > 0 && (
        <ul className="mt-4 grid gap-2 sm:grid-cols-3">
          {gains.map((gain) => (
            <li
              key={gain}
              className="flex items-start gap-2 rounded-lg border border-outline bg-surface-variant/30 px-3 py-2 text-sm text-on-background"
            >
              <ArrowUp size={14} weight="bold" className="mt-0.5 shrink-0 text-primary" aria-hidden />
              <span className="min-w-0">{gain}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-5">
        {upgrade.selfServe ? (
          <Button onClick={change} disabled={busy}>
            {busy ? "Changing…" : `Switch to ${label}`}
          </Button>
        ) : (
          <Button variant="secondary" onClick={() => navigate("/pricing")}>
            See the {label} plan
          </Button>
        )}
      </div>

      {confirmation && (
        <div
          role="status"
          className="mt-4 rounded-lg border border-outline bg-surface-variant/40 px-3 py-2.5"
        >
          <p className="text-sm font-medium text-on-background">{confirmation.title}</p>
          <p className="mt-0.5 text-sm text-muted">{confirmation.detail}</p>
        </div>
      )}
    </Card>
  );
}
