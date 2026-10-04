/**
 * In-app card management.
 *
 * The Stripe Customer Portal's card screen cannot be branded, so this mounts
 * Stripe's Payment Element against a **Customer Session** inside our own layout.
 * Stripe still hosts and renders the card fields in an iframe, so the PCI scope
 * is unchanged (SAQ A) — only the chrome around it is ours.
 *
 * What this owns:
 *  - the saved-card list (brand, wallet, expiry, and which card a renewal will
 *    charge) from `GET /payment-methods`;
 *  - "set as default" and "remove", both of which take the **list the API
 *    returns** as the new state so there is no second read to race — removal is
 *    confirmed in a styled `Modal` and the API's 409 for a default card an
 *    active subscription depends on is shown verbatim;
 *  - adding a card, behind a disclosure rather than always-on: mounting Stripe's
 *    iframe is not free and most visits are not adding one. It opens by itself
 *    for an account with no card on file, because then the form *is* the
 *    section's content;
 *  - expiry warnings, because a lapsed card fails a renewal silently.
 *
 * Every rule that decides *behaviour* — what a card says, whether it is
 * expiring, whether the Element can mount, what its options are — lives in
 * `lib/paymentMethod` and is unit-tested; this file renders the result.
 */

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { CreditCard, Lock, Plus } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Modal } from "@/components/Modal";
import { trustedExternalUrl } from "@/lib/download";
import { embeddedCheckoutEnabled, publishableKey } from "@/lib/stripeCheckout";
import {
  CARD_FORM_FAILED_MESSAGE,
  PAYMENT_METHOD_SAVED_MESSAGE,
  addCardPrompt,
  billingDefaults,
  cardExpiry,
  cardTitle,
  confirmPaymentMethodSetup,
  defaultCard,
  needsPortalFallback,
  paymentElementElementOptions,
  paymentElementOptions,
  paymentMethodPanel,
  paymentMethodSectionVisible,
  walletLabel,
} from "@/lib/paymentMethod";
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

/**
 * The form inside `<Elements>`: the stripe/elements hooks only work there.
 *
 * The submit itself lives in `lib/paymentMethod.ts` (`confirmPaymentMethodSetup`)
 * because this component cannot be rendered in this repo's Node test
 * environment — no jsdom, no real Stripe.js. All this keeps is the request, the
 * saving flag, and the rendering of the result.
 */
function PaymentMethodForm({
  onSaved,
  onCancel,
}: {
  onSaved: () => void;
  onCancel: () => void;
}) {
  const stripe = useStripe();
  const elements = useElements();
  const { user } = useAuth();
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
      success(PAYMENT_METHOD_SAVED_MESSAGE);
      onSaved();
    } else {
      error(result.message);
    }
  }

  return (
    <form onSubmit={save} className="mt-4 space-y-4">
      {/* Prefills what the account already knows (cardholder name, email) so the
          customer is not asked twice for something we already hold. */}
      <PaymentElement options={paymentElementElementOptions(billingDefaults(user))} />
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!stripe || !elements || saving}>
          {saving ? "Saving…" : "Save card"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

export function PaymentMethodSection({ hasSubscription }: { hasSubscription: boolean }) {
  const { api: client } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();
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
  /** In-flight state for the Stripe-hosted tax / billing-address page. */
  const [detailsLoading, setDetailsLoading] = useState(false);
  /** Whether the add-card form is showing. */
  const [formOpen, setFormOpen] = useState(false);
  /**
   * Set when Stripe.js threw while rendering the Payment Element.
   *
   * Stripe reports a malformed secret or an unusable configuration by
   * *throwing*, and without the boundary below that unmounts the whole Billing
   * page — leaving the customer on a blank screen exactly where they came to
   * fix their payment. This records the failure so the section can explain it
   * and offer the portal instead.
   */
  const [formFailed, setFormFailed] = useState(false);

  /**
   * Open the Stripe Customer Portal for the one thing this page does not own.
   *
   * Plan changes, cards and invoices are all in-app now, but a customer's tax id
   * and billing address are only editable in Stripe's portal, so that capability
   * is kept as a quiet secondary link rather than dropped. The URL is guarded
   * exactly like every other API-supplied redirect.
   */
  async function openBillingDetails() {
    if (detailsLoading) return;
    setDetailsLoading(true);
    try {
      const { portal_url } = await client.createPortalSession();
      const target = trustedExternalUrl(portal_url);
      if (!target) throw new Error("The billing portal link was not valid. Please try again.");
      window.location.assign(target);
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      if (message.toLowerCase().includes("no stripe customer")) {
        navigate("/pricing");
      } else {
        error(message || "Could not open the billing portal");
      }
    } finally {
      setDetailsLoading(false);
    }
  }

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

  const listLoaded = cards !== null || cardsFailure !== null;
  const enabled = cards?.enabled === true;
  const methods = enabled ? (cards?.methods ?? []) : [];
  const noCards = enabled && methods.length === 0;

  // A brand-new account has nothing to show, so the form is the section's
  // content rather than something to go looking for. Removing the last card
  // reopens it for the same reason, and collapsing it by hand still works
  // because this only runs when `noCards` itself changes.
  useEffect(() => {
    if (noCards) setFormOpen(true);
  }, [noCards]);

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

  const primary = defaultCard(methods);
  const prompt = addCardPrompt(methods);
  const mutating = busyId !== null;
  const mount = panel.kind === "mount" ? panel : null;
  // The form is unusable either because this build cannot mount it, or because
  // Stripe refused it. Both leave the portal as the honest way to add a card.
  const formUnavailable = Boolean(mount) && !formFailed;
  const showPortalFallback = needsPortalFallback(panel) || formFailed;

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-display text-lg font-semibold">Payment method</h2>
          <p className="mt-1 text-sm text-muted">The card we charge when your plan renews.</p>
        </div>
        <CreditCard size={20} className="mt-0.5 shrink-0 text-muted" aria-hidden />
      </div>

      {/* Saved cards — only when the API says the account can manage them.
          `enabled: false` is the same ordinary state the session reports, and
          its explanation is already rendered by the mount panel below. */}
      {enabled && !listLoaded && <Skeleton className="mt-5 h-16 w-full" />}

      {enabled && listLoaded && methods.length > 0 && (
        <>
          {/* Says outright which card is charged, so the "Default" badge is never
              the only clue. Omitted when nothing is flagged: claiming a card
              will be charged when Stripe did not say so is worse than silence. */}
          {primary && (
            <p className="mt-5 text-xs text-muted">
              Next payment uses{" "}
              <span className="font-medium text-on-background">{cardTitle(primary)}</span>.
            </p>
          )}
          <ul
            className={`divide-y divide-outline overflow-hidden rounded-lg border border-outline ${
              primary ? "mt-3" : "mt-5"
            }`}
            aria-busy={mutating}
            aria-label="Saved cards"
          >
            {methods.map((card) => (
              <CardRow
                key={card.id}
                card={card}
                busy={busyId === card.id}
                // Already default, or another card's action is in flight: the
                // endpoints return a whole new list, so overlapping writes
                // would let an older response win.
                actionsDisabled={mutating}
                onSetDefault={() => setDefault(card.id)}
                onRemove={() => openRemoveConfirm(card)}
              />
            ))}
          </ul>
        </>
      )}

      {cardsFailure && <p className="mt-3 text-sm text-muted">{cardsFailure}</p>}

      {panel.kind === "loading" && <Skeleton className="mt-4 h-11 w-full" />}

      {/* The reason to add a card. Factual, not an upsell: with none on file a
          paid plan cannot renew, and with one a single decline interrupts it.
          Only shown when the form can actually mount — inviting someone to add
          a card in a build that cannot take one would be a false instruction. */}
      {formUnavailable && prompt && <p className="mt-4 text-xs text-muted">{prompt}</p>}

      {panel.kind === "explain" && <p className="mt-2 text-sm text-muted">{panel.message}</p>}

      {formFailed && (
        <p role="alert" className="mt-2 text-sm text-muted">
          {CARD_FORM_FAILED_MESSAGE}
        </p>
      )}

      {mount && !formFailed && (
        <div className="mt-4">
          {formOpen ? (
            <>
              {/* `paymentElementOptions` owns the clientSecret-vs-mode choice:
                  the two are mutually exclusive and passing both is an
                  integration error rather than a preference. */}
              <ErrorBoundary
                // Reopening the form is what retries it.
                resetKey={formOpen}
                onError={() => setFormFailed(true)}
                fallback={null}
              >
                <Elements
                  stripe={stripePromise}
                  options={paymentElementOptions(
                    mount.clientSecret,
                    session?.setup_intent_client_secret,
                  )}
                >
                  <PaymentMethodForm
                    onSaved={() => {
                      setFormOpen(false);
                      loadCards();
                    }}
                    onCancel={() => setFormOpen(false)}
                  />
                </Elements>
              </ErrorBoundary>
              <p className="mt-4 flex items-center gap-1.5 text-xs text-muted">
                <Lock size={13} weight="fill" aria-hidden />
                Card details go straight to Stripe and never touch our servers.
              </p>
            </>
          ) : (
            <Button variant="secondary" size="sm" onClick={() => setFormOpen(true)}>
              <Plus size={15} weight="bold" aria-hidden />
              Add payment method
            </Button>
          )}
        </div>
      )}

      {/* A build that cannot mount the card form, or a Stripe.js failure, would
          otherwise leave adding a card impossible here. Stripe's portal still
          can, and it is guarded and error-handled exactly like the tax link. */}
      {showPortalFallback && (
        <div className="mt-4">
          <Button variant="secondary" size="sm" onClick={openBillingDetails} disabled={detailsLoading}>
            <Plus size={15} weight="bold" aria-hidden />
            {detailsLoading ? "Opening…" : "Add payment method"}
          </Button>
        </div>
      )}

      {/* Tax identity and the billing address are the one part of billing this
          app does not own, so they stay reachable through Stripe's portal. A
          quiet line rather than a button: it is a lookup, not a decision. */}
      {hasSubscription && (
        <div className="mt-6 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-outline pt-4">
          <p className="text-xs text-muted">Invoices are issued to the details on file.</p>
          <button
            type="button"
            onClick={openBillingDetails}
            disabled={detailsLoading}
            className="text-xs font-medium text-muted transition-colors hover:text-on-background disabled:opacity-50"
          >
            {detailsLoading ? "Opening…" : "Tax details & billing address"}
          </button>
        </div>
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
          {removing ? `${cardTitle(removing)} will be removed from your account.` : ""}
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

/** One saved card: what it is, whether it still works, and what can be done. */
function CardRow({
  card,
  busy,
  actionsDisabled,
  onSetDefault,
  onRemove,
}: {
  card: SavedPaymentMethodResponse;
  busy: boolean;
  actionsDisabled: boolean;
  onSetDefault: () => void;
  onRemove: () => void;
}) {
  const expiry = cardExpiry(card);
  const wallet = walletLabel(card.wallet);
  const title = cardTitle(card);

  return (
    <li className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <CreditCard size={18} className="shrink-0 text-muted" aria-hidden />
        <span className="text-sm text-on-background">{title}</span>
        {wallet && <Badge color="var(--color-muted)">{wallet}</Badge>}
        {card.is_default && <Badge color="var(--color-primary)">Default</Badge>}
        {/* Only a real problem gets a coloured badge, so the warning stands out
            when it appears; a healthy expiry is plain text. */}
        {expiry.state === "expired" ? (
          <Badge color="var(--color-error)">Expired</Badge>
        ) : expiry.state === "expiring" ? (
          <Badge color="var(--color-warning)">Expiring soon</Badge>
        ) : null}
        {expiry.label && (
          <span className="text-xs text-muted">
            {/* The badge already says "Expired", so repeating the word beside it
                reads as a stutter. */}
            {expiry.state === "expired" ? "" : "Expires "}
            {expiry.label}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onSetDefault}
          disabled={card.is_default || actionsDisabled}
          aria-label={`Set ${title} as default`}
        >
          {busy ? "Updating…" : "Set as default"}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={onRemove}
          disabled={actionsDisabled}
          aria-label={`Remove ${title}`}
        >
          Remove
        </Button>
      </div>
    </li>
  );
}
