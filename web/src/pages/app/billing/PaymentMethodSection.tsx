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
 *
 * Saved cards are listed from `GET /payment-methods`; "set as default" and
 * "remove" call the API and take the **list it returns** as the new state, so
 * there is no second read to race. Removal is confirmed in a styled `Modal`
 * (`window.confirm` is used nowhere in this app), and the API's 409 for a
 * default card an active subscription depends on is shown verbatim.
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { CreditCard, Lock } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { embeddedCheckoutEnabled, publishableKey } from "@/lib/stripeCheckout";
import {
  confirmPaymentMethodSetup,
  paymentMethodPanel,
  paymentMethodSectionVisible,
} from "@/lib/paymentMethod";
import { STRIPE_CHECKOUT_APPEARANCE } from "@/lib/stripeAppearance";
import type {
  PaymentMethodListResponse,
  PaymentMethodSessionResponse,
  SavedPaymentMethodResponse,
} from "@/api/types";

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

/** A card's expiry as `MM/YYYY`, zero-padded the way a card prints it. */
function formatExpiry(card: SavedPaymentMethodResponse): string {
  return `${String(card.exp_month).padStart(2, "0")}/${card.exp_year}`;
}

/**
 * The form inside `<Elements>`: the stripe/elements hooks only work there.
 *
 * The submit itself lives in `lib/paymentMethod.ts` (`confirmPaymentMethodSetup`)
 * because this component cannot be rendered in this repo's Node test
 * environment — no jsdom, no real Stripe.js. All this keeps is the request, the
 * saving flag, and the rendering of the result.
 */
function PaymentMethodForm({ onSaved }: { onSaved: () => void }) {
  const stripe = useStripe();
  const elements = useElements();
  const { success, error } = useToast();
  const [saving, setSaving] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    const result = await confirmPaymentMethodSetup({
      stripe,
      elements,
      // `if_required` keeps the user on the page for a card; a redirect-based
      // method still sends them to its own authorization screen and back here.
      returnUrl: window.location.href,
    });
    setSaving(false);
    if (result.ok) {
      success("Payment method saved");
      onSaved();
    } else {
      error(result.message);
    }
  }

  return (
    <form onSubmit={save} className="mt-4 space-y-4">
      <PaymentElement />
      <Button type="submit" variant="secondary" disabled={!stripe || !elements || saving}>
        {saving ? "Saving…" : "Save payment method"}
      </Button>
    </form>
  );
}

export function PaymentMethodSection({ hasSubscription }: { hasSubscription: boolean }) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const [session, setSession] = useState<PaymentMethodSessionResponse | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [cards, setCards] = useState<PaymentMethodListResponse | null>(null);
  const [cardsFailure, setCardsFailure] = useState<string | null>(null);
  /** The card a set-default request is in flight for, if any. */
  const [busyId, setBusyId] = useState<string | null>(null);
  /** The card whose removal is being confirmed in the modal, if any. */
  const [removing, setRemoving] = useState<SavedPaymentMethodResponse | null>(null);
  const [removingBusy, setRemovingBusy] = useState(false);
  /** The API's own refusal message (e.g. the 409), shown inside the modal. */
  const [removeError, setRemoveError] = useState<string | null>(null);

  const hasPublishableKey = embeddedCheckoutEnabled();

  /**
   * Read the saved cards.
   *
   * Also called after the Payment Element saves a card: that request goes
   * straight to Stripe and never passes through the API client, so there is
   * nothing to invalidate and the list has to be re-read (see
   * `listPaymentMethods`, which is deliberately uncached for this reason).
   */
  const loadCards = useCallback(() => {
    client
      .listPaymentMethods()
      .then((next) => {
        setCards(next);
        setCardsFailure(null);
      })
      .catch((err: Error) => {
        setCards(null);
        setCardsFailure(err.message);
      });
  }, [client]);

  useEffect(() => {
    // No subscription means no Stripe customer, so there is nothing to manage.
    if (!hasSubscription) {
      setSession(null);
      setFailure(null);
      setCards(null);
      setCardsFailure(null);
      return;
    }

    // The saved-card list is a plain authenticated read and is useful even in a
    // build with no publishable key: choosing a default or removing a card does
    // not need Stripe.js. Only the *session* is skipped without a key — minting
    // a short-lived secret that could never be used is what that guard prevents.
    loadCards();

    if (!hasPublishableKey) {
      setSession(null);
      setFailure(null);
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
  }, [client, hasSubscription, hasPublishableKey, loadCards]);

  const panel = paymentMethodPanel({
    hasSubscription,
    hasPublishableKey,
    session,
    failure,
  });

  if (!paymentMethodSectionVisible(panel)) return null;

  /** Replace the list with what a mutation just returned — no second read. */
  function applyCards(next: PaymentMethodListResponse) {
    setCards(next);
    setCardsFailure(null);
  }

  async function setDefault(id: string) {
    if (busyId) return;
    setBusyId(id);
    try {
      applyCards(await client.setDefaultPaymentMethod(id));
      success("Default payment method updated");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not update the default card");
    } finally {
      setBusyId(null);
    }
  }

  function openRemoveConfirm(card: SavedPaymentMethodResponse) {
    setRemoveError(null);
    setRemoving(card);
  }

  function closeRemoveConfirm() {
    setRemoveError(null);
    setRemoving(null);
  }

  async function confirmRemove() {
    if (!removing || removingBusy) return;
    setRemovingBusy(true);
    setRemoveError(null);
    try {
      applyCards(await client.removePaymentMethod(removing.id));
      success("Payment method removed");
      setRemoving(null);
    } catch (err) {
      // A 409 says this card is the default for an active subscription and that
      // another card must be made default first. That sentence is the whole
      // explanation the user needs, so it is shown verbatim — flattening it into
      // "Could not remove that card" would hide the one action that fixes it.
      setRemoveError(
        err instanceof Error && err.message ? err.message : "Could not remove that card.",
      );
    } finally {
      setRemovingBusy(false);
    }
  }

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

      {/* Saved cards — only when the API says the account can manage them.
          `enabled: false` is the same ordinary state the session reports, and
          its explanation is already rendered by the mount panel below. */}
      {cards?.enabled && (
        <div className="mt-5">
          <h3 className="text-sm font-medium">Saved cards</h3>
          {cards.methods.length === 0 ? (
            <p className="mt-2 text-sm text-muted">No cards on file yet.</p>
          ) : (
            <ul className="mt-3 divide-y divide-outline overflow-hidden rounded-lg border border-outline">
              {cards.methods.map((card) => (
                <li
                  key={card.id}
                  className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <CreditCard size={16} className="text-muted" aria-hidden />
                    <span className="text-sm text-on-background capitalize">
                      {card.brand} •••• {card.last4}
                    </span>
                    <span className="text-xs text-muted">Expires {formatExpiry(card)}</span>
                    {card.is_default && <Badge color="var(--color-primary)">Default</Badge>}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDefault(card.id)}
                      // Already default, or another card's action is in flight:
                      // the endpoints return a whole new list, so overlapping
                      // writes would let an older response win.
                      disabled={card.is_default || busyId !== null}
                      aria-label={`Set ${card.brand} ending ${card.last4} as default`}
                    >
                      {busyId === card.id ? "Updating…" : "Set as default"}
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => openRemoveConfirm(card)}
                      disabled={busyId !== null}
                      aria-label={`Remove ${card.brand} ending ${card.last4}`}
                    >
                      Remove
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {cardsFailure && <p className="mt-3 text-sm text-muted">{cardsFailure}</p>}

      {panel.kind !== "explain" && (
        <h3 className="mt-6 text-sm font-medium">Add a payment method</h3>
      )}

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
              // Branded, not Stripe's stock dark theme. The Appearance API takes
              // literal values (its iframe cannot resolve our CSS custom
              // properties), which is why the palette lives as hex in exactly one
              // place — `lib/stripeAppearance` — pinned to `@theme` by a test.
              appearance: STRIPE_CHECKOUT_APPEARANCE,
            }}
          >
            <PaymentMethodForm onSaved={loadCards} />
          </Elements>
          <p className="mt-4 flex items-center gap-1.5 text-xs text-muted">
            <Lock size={13} weight="fill" aria-hidden />
            Card details go straight to Stripe and never touch our servers.
          </p>
        </>
      )}

      {/* Destructive actions are confirmed in a dialog, never `window.confirm`. */}
      <Modal
        open={removing !== null}
        onClose={() => {
          if (!removingBusy) closeRemoveConfirm();
        }}
        title="Remove payment method?"
        maxWidth="max-w-sm"
      >
        <p className="text-sm text-on-background">
          {removing
            ? `${removing.brand} •••• ${removing.last4} will be removed from your account.`
            : ""}
        </p>
        {removeError && (
          <p
            role="alert"
            className="mt-3 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error"
          >
            {removeError}
          </p>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={closeRemoveConfirm} disabled={removingBusy}>
            Keep card
          </Button>
          <Button variant="destructive" onClick={confirmRemove} disabled={removingBusy}>
            {removingBusy ? "Removing…" : "Remove card"}
          </Button>
        </div>
      </Modal>
    </Card>
  );
}
