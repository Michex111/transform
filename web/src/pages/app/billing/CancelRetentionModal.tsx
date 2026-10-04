/**
 * The retention wizard behind "Cancel subscription".
 *
 * Three steps — reason, tailored save offer, confirmation — driven entirely by
 * the pure rules in `lib/cancelFlow`. Nothing here decides copy or which offer
 * to show; it only asks the API and renders the result.
 *
 * Two rules are load-bearing:
 *  - A save offer that succeeds closes the modal and does **not** cancel.
 *  - Every offer screen carries a quiet "Continue to cancel", so the user is
 *    never trapped, and the only control that cancels is on the last step.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { describePlanChange, planChangeFailure } from "@/lib/planChange";
import {
  CANCEL_REASONS,
  cancelStepCount,
  cancelStepNumber,
  cancelSteps,
  cancellationOutcome,
  canLeaveReasonStep,
  nextCancelStep,
  previousCancelStep,
  saveOfferFor,
  type CancelReasonId,
  type CancelStep,
} from "@/lib/cancelFlow";
import type {
  CreditBalanceResponse,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
} from "@/api/types";

const STEP_LABEL: Record<CancelStep, string> = {
  reason: "Reason",
  offer: "Offer",
  confirm: "Confirm",
};

export interface CancelRetentionModalProps {
  open: boolean;
  onClose: () => void;
  plan: SubscriptionStatusResponse | null;
  credit: CreditBalanceResponse | null;
  plans: SubscriptionPlanResponse[];
  /** Re-reads the plan, wallet and history after a cancel or a saved plan. */
  onChanged: () => void;
}

export function CancelRetentionModal({
  open,
  onClose,
  plan,
  credit,
  plans,
  onChanged,
}: CancelRetentionModalProps) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();

  const [step, setStep] = useState<CancelStep>("reason");
  const [reason, setReason] = useState<CancelReasonId | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // A fresh wizard every time it opens; a stale "confirm" step would be a
  // dangerous thing to land on.
  useEffect(() => {
    if (!open) return;
    setStep("reason");
    setReason(null);
    setBusy(false);
    setFailure(null);
  }, [open]);

  const steps = cancelSteps(reason);
  // A reason that skips the offer can never leave the wizard on an offer step,
  // but guard anyway so a later reason change could not strand the UI.
  const current: CancelStep = steps.includes(step) ? step : steps[steps.length - 1];

  function goNext() {
    const next = nextCancelStep(current, reason);
    if (next) setStep(next);
  }

  function goBack() {
    const previous = previousCancelStep(current, reason);
    if (previous) setStep(previous);
  }

  async function cancelSubscription() {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await client.cancelSubscription();
      success("Subscription cancelled");
      onChanged();
      onClose();
    } catch (err) {
      setFailure(err instanceof Error ? err.message : "Could not cancel your subscription.");
    } finally {
      setBusy(false);
    }
  }

  async function saveWithPlan(tier: string) {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await client.changePlan(tier);
      const feedback = describePlanChange(result);
      success(`${feedback.title} — ${feedback.detail}`);
      onChanged();
      onClose();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      const message = err instanceof Error ? err.message : "";
      const failureInfo = planChangeFailure(status, message);
      setFailure(failureInfo.message);
      error(failureInfo.message);
      if (failureInfo.goToPricing) {
        onClose();
        navigate("/pricing");
      }
    } finally {
      setBusy(false);
    }
  }

  function goToSupport() {
    onClose();
    navigate("/app/support");
  }

  const offer = reason ? saveOfferFor(reason, { currentTier: plan?.tier ?? null, plans }) : null;
  // "Something else" has no offer, so the step-transition rules skip the offer
  // step entirely. The second clause is only a guard for an unreachable state;
  // keying off `offer.kind` alone would show the confirmation alongside the
  // reason radios the moment "Something else" is selected.
  const showConfirm = current === "confirm" || (current === "offer" && offer?.kind === "none");

  const outcome = cancellationOutcome({
    currentPeriodEnd: plan?.current_period_end ?? null,
    planCredits: credit?.plan_remaining ?? credit?.monthly_remaining ?? null,
    carryoverCredits: credit?.carryover_credits ?? null,
    purchasedCredits: credit?.purchased_credits ?? null,
  });

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Before you cancel"
      maxWidth="max-w-md"
    >
      <ol className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted" aria-label="Cancellation steps">
        {steps.map((s, i) => (
          <li
            key={s}
            aria-current={s === current ? "step" : undefined}
            className={s === current ? "font-semibold text-on-background" : undefined}
          >
            {i + 1}. {STEP_LABEL[s]}
          </li>
        ))}
      </ol>

      {failure && (
        <p
          role="alert"
          className="mt-4 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error"
        >
          {failure}
        </p>
      )}

      {/* Step 1 — reason */}
      {current === "reason" && (
        <div className="mt-4">
          <p className="text-sm text-on-background">
            What's the main reason you're thinking of cancelling?
          </p>
          <div
            role="radiogroup"
            aria-labelledby="cancel-reason-legend"
            className="mt-3 space-y-2"
          >
            <span id="cancel-reason-legend" className="sr-only">
              Reason for cancelling
            </span>
            {CANCEL_REASONS.map((option) => (
              <label
                key={option.id}
                className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                  reason === option.id
                    ? "border-primary bg-primary/10"
                    : "border-outline hover:bg-surface-variant/50"
                }`}
              >
                <input
                  type="radio"
                  name="cancel-reason"
                  value={option.id}
                  checked={reason === option.id}
                  onChange={() => setReason(option.id)}
                  className="h-4 w-4 shrink-0 accent-[var(--color-primary)]"
                />
                <span className="text-on-background">{option.label}</span>
              </label>
            ))}
          </div>

          <div className="mt-6 flex items-center justify-between gap-2">
            <span className="text-xs text-muted">
              Step {cancelStepNumber(current, reason)} of {cancelStepCount(reason)}
            </span>
            <Button onClick={goNext} disabled={!canLeaveReasonStep(reason)}>
              Continue
            </Button>
          </div>
        </div>
      )}

      {/* Step 2 — the tailored save offer */}
      {current === "offer" && offer && offer.kind !== "none" && (
        <div className="mt-4">
          <h3 className="font-display text-base font-semibold text-on-background">{offer.title}</h3>
          <p className="mt-1.5 text-sm text-muted">{offer.body}</p>

          <div className="mt-5 flex flex-col gap-2">
            {offer.kind === "change-plan" && (
              <Button onClick={() => saveWithPlan(offer.tier)} disabled={busy}>
                {busy ? "Changing…" : offer.cta}
              </Button>
            )}
            {offer.kind === "free" && (
              <Button onClick={onClose} disabled={busy}>
                {offer.cta}
              </Button>
            )}
            {offer.kind === "support" && (
              <Button variant="secondary" onClick={goToSupport} disabled={busy}>
                {offer.cta}
              </Button>
            )}
            <button
              type="button"
              onClick={() => setStep("confirm")}
              disabled={busy}
              className="self-center text-xs font-medium text-muted underline-offset-4 hover:text-on-background hover:underline disabled:opacity-50"
            >
              Continue to cancel
            </button>
          </div>

          <div className="mt-5 flex items-center justify-between gap-2 border-t border-outline pt-4">
            <Button variant="ghost" size="sm" onClick={goBack} disabled={busy}>
              Back
            </Button>
            <span className="text-xs text-muted">
              Step {cancelStepNumber(current, reason)} of {cancelStepCount(reason)}
            </span>
          </div>
        </div>
      )}

      {/* Step 3 — exactly what happens */}
      {showConfirm && (
        <div className="mt-4">
          <h3 className="font-display text-base font-semibold text-on-background">
            {outcome.headline}
          </h3>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            {outcome.lines.map((line) => (
              <li key={line} className="flex items-start gap-2">
                <span aria-hidden className="mt-[0.45rem] h-1 w-1 shrink-0 rounded-full bg-muted" />
                <span className="min-w-0">{line}</span>
              </li>
            ))}
          </ul>

          <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
            <Button variant="ghost" size="sm" onClick={goBack} disabled={busy}>
              Back
            </Button>
            <div className="flex flex-wrap justify-end gap-2">
              <Button onClick={onClose} disabled={busy}>
                Keep my plan
              </Button>
              <Button variant="destructive" onClick={cancelSubscription} disabled={busy}>
                {busy ? "Cancelling…" : "Cancel subscription"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
