/**
 * In-app card management.
 *
 * The Stripe Customer Portal's card screen cannot be branded, so this mounts
 * Stripe's Payment Element against a **Customer Session** inside our own layout.
 * Stripe still hosts and renders the card fields in an iframe, so the PCI scope
 * is unchanged (SAQ A) — only the chrome around it is ours.
 *
 * Deliberately secondary to changing the plan: it is a quiet card near the
 * bottom of the page, and it renders a plain explanatory line (never a broken
 * element) whenever the build or the account cannot mount it.
 */

import { useEffect, useState, type FormEvent } from "react";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { CreditCard, Lock } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { embeddedCheckoutEnabled, publishableKey } from "@/lib/stripeCheckout";
import { paymentMethodPanel, paymentMethodSectionVisible } from "@/lib/paymentMethod";
import type { PaymentMethodSessionResponse } from "@/api/types";

/**
 * Stripe.js is loaded once per page life, exactly as `CheckoutPage` does it.
 * `loadStripe` injects a `<script>` tag on every call, so calling it per mount
 * would add a duplicate on every StrictMode pass and hot reload.
 *
 * Evaluated at module scope and only when a real `pk_` key exists: handing
 * `loadStripe` an empty string throws here, at import time, which would take the
 * whole billing route down. `null` means "never mount", not "mount with nothing".
 *
 * The account is on API version `2026-07-29.dahlia`, where the *embedded
 * checkout* entry point is named `createEmbeddedCheckoutPage` (the old
 * `initEmbeddedCheckout` throws). This section uses the Elements/Payment Element
 * API instead, whose names are unchanged: `loadStripe`, `Elements`,
 * `PaymentElement`, `useStripe`, `useElements`, `stripe.confirmSetup`.
 */
const stripePromise: Promise<Stripe | null> | null = embeddedCheckoutEnabled()
  ? loadStripe(publishableKey())
  : null;

/** The form inside `<Elements>`: the stripe/elements hooks only work there. */
function PaymentMethodForm() {
  const stripe = useStripe();
  const elements = useElements();
  const { success, error } = useToast();
  const [saving, setSaving] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!stripe || !elements || saving) return;
    setSaving(true);
    try {
      // `if_required` keeps the user on the page for a card; a redirect-based
      // method still sends them to its own authorization screen and back here.
      const { error: stripeError } = await stripe.confirmSetup({
        elements,
        redirect: "if_required",
        confirmParams: { return_url: window.location.href },
      });
      if (stripeError) throw new Error(stripeError.message || "Could not save that card.");
      success("Payment method saved");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not save that card.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="mt-4 space-y-4">
      {/* `night` is Stripe's built-in dark theme. The appearance API needs
          literal colour values and cannot take our `var(--token)` classes, so
          using the built-in theme avoids duplicating the palette as hex here. */}
      <PaymentElement />
      <Button type="submit" variant="secondary" disabled={!stripe || !elements || saving}>
        {saving ? "Saving…" : "Save payment method"}
      </Button>
    </form>
  );
}

export function PaymentMethodSection({ hasSubscription }: { hasSubscription: boolean }) {
  const { api: client } = useAuth();
  const [session, setSession] = useState<PaymentMethodSessionResponse | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const hasPublishableKey = embeddedCheckoutEnabled();

  useEffect(() => {
    // No subscription means no Stripe customer; no key means Stripe.js cannot be
    // mounted. Skipping the request in either case also avoids minting a
    // short-lived session secret that could never be used.
    if (!hasSubscription || !hasPublishableKey) {
      setSession(null);
      return;
    }
    let active = true;
    client
      .paymentMethodSession()
      .then((next) => {
        if (!active) return;
        setSession(next);
        setFailure(null);
      })
      .catch((err: Error) => {
        if (!active) return;
        setSession(null);
        setFailure(err.message);
      });
    return () => {
      active = false;
    };
  }, [client, hasSubscription, hasPublishableKey]);

  const panel = paymentMethodPanel({
    hasSubscription,
    hasPublishableKey,
    session,
    failure,
  });

  if (!paymentMethodSectionVisible(panel)) return null;

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-lg font-semibold">Payment method</h2>
          <p className="mt-1 text-sm text-muted">
            Update the card we charge for your subscription.
          </p>
        </div>
        <CreditCard size={20} className="mt-0.5 shrink-0 text-muted" aria-hidden />
      </div>

      {panel.kind === "loading" && <Skeleton className="mt-4 h-11 w-full" />}

      {panel.kind === "explain" && <p className="mt-4 text-sm text-muted">{panel.message}</p>}

      {panel.kind === "mount" && (
        <>
          <Elements
            stripe={stripePromise}
            options={{
              mode: "setup",
              // Every price in this product is quoted in USD, so the SetupIntent
              // Stripe creates for a saved method is too.
              currency: "usd",
              customerSessionClientSecret: panel.clientSecret,
              appearance: { theme: "night" },
            }}
          >
            <PaymentMethodForm />
          </Elements>
          <p className="mt-4 flex items-center gap-1.5 text-xs text-muted">
            <Lock size={13} weight="fill" aria-hidden />
            Card details go straight to Stripe and never touch our servers.
          </p>
        </>
      )}
    </Card>
  );
}
