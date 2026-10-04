/**
 * Plan management inside our own UI.
 *
 * Replaces the Customer Portal's "change plan" screen, which cannot be branded.
 * Everything goes through `changePlan`, never `checkout` — creating a second
 * subscription would double-bill the customer.
 *
 * Today only PRO reaches this card, so the only button it renders is an upgrade:
 * an upgrade applies immediately (the API prorates) and converts the unspent
 * plan balance into expiring carryover. Pro Plus has nothing above it, so
 * `planChangeOptions` returns no options for it at all and the billing page
 * omits the section — that customer's "Change plan" button goes to the pricing
 * page, where the downgrade is offered against the existing subscription. The
 * rendering below still handles a downgrade option because it renders whatever
 * the model offers; the model is where the tier rule lives.
 *
 * All the decisions (which actions to offer, what the confirmation says, where
 * a failure sends the user) live in `lib/planChange` and are unit-tested; this
 * component only calls the API and renders the result.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp } from "@phosphor-icons/react";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card } from "@/components/ui";
import { describePlanChange, planChangeExplainer, planChangeFailure, planChangeOptions } from "@/lib/planChange";

export function PlanChangeCard({
  currentTier,
  onChanged,
}: {
  /** The account's current tier, or `null` while the page data loads. */
  currentTier: string | null;
  /** Re-reads the page data after a successful change. */
  onChanged: () => void;
}) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
  const [busyTier, setBusyTier] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{
    title: string;
    detail: string;
  } | null>(null);

  const options = planChangeOptions(currentTier);
  // Nothing to offer a Free account (no subscription to change), an Enterprise
  // one (not self-serve) or Pro Plus (nothing above it), so the section is
  // omitted entirely — `BillingPage` checks the same rule before rendering the
  // wrapper, so no empty gap is left in the page's stack either.
  if (options.length === 0) return null;

  async function change(tier: string) {
    if (busyTier) return;
    setBusyTier(tier);
    setConfirmation(null);
    try {
      const result = await client.changePlan(tier);
      const feedback = describePlanChange(result);
      // Kept on screen as well as toasted: the carryover is money the user
      // cares about, and a toast disappears after a few seconds.
      setConfirmation({ title: feedback.title, detail: feedback.detail });
      success(`${feedback.title} — ${feedback.detail}`);
      // Re-read the plan, credits and history so every card agrees with the
      // change that just happened.
      onChanged();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      const message = err instanceof Error ? err.message : "";
      const failure = planChangeFailure(status, message);
      error(failure.message);
      // A 409 means there is no subscription to change — start one instead.
      if (failure.goToPricing) navigate("/pricing");
    } finally {
      setBusyTier(null);
    }
  }

  return (
    <Card hover className="p-6">
      <h2 className="font-display text-lg font-semibold">Change plan</h2>
      {/* The proration and any carryover are explained *before* the click: both
          are money, and a confirmation that only appears afterwards cannot help
          someone decide. The words come from `planChangeExplainer(options)`, so
          they only ever describe the buttons actually rendered below — see the
          function's own note. The exact carryover number still comes from the
          response, since only the server knows what was left. */}
      <p className="mt-1 text-sm text-muted">{planChangeExplainer(options)}</p>

      <div className="mt-4 flex flex-wrap gap-2">
        {options.map((option) => {
          const upgrade = option.direction === "upgrade";
          const Icon = upgrade ? ArrowUp : ArrowDown;
          const busy = busyTier === option.tier;
          return (
            <Button
              key={option.tier}
              variant={upgrade ? "primary" : "secondary"}
              onClick={() => change(option.tier)}
              // One change at a time: a second request while the first is in
              // flight could race the tier guard the API relies on.
              disabled={busyTier !== null}
            >
              <Icon size={15} weight="bold" aria-hidden />
              {busy
                ? "Changing…"
                : `${upgrade ? "Upgrade" : "Switch"} to ${option.label}`}
            </Button>
          );
        })}
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
