/**
 * Whether — and how — the Billing page can show in-app card management.
 *
 * The Customer Portal's card screen cannot be branded, so the page mounts
 * Stripe's Payment Element against a Customer Session instead. Whether that is
 * possible depends on things only the API and the build know, and getting it
 * wrong means a blank iframe or a button that throws — so the decision lives
 * here, pure and unit-tested, and the component only renders the result.
 *
 * Deliberately pure and free of React and of Stripe.js *at runtime*: this repo's
 * test environment is Node with no jsdom, so importing either would make the
 * rules untestable. The one Stripe.js reference below is an `import type`, which
 * is erased at build time — the submit logic it types is exercised with plain
 * object fakes, not a DOM or a real Stripe.js instance.
 */

import type { Stripe, StripeElements } from "@stripe/stripe-js";

export interface PaymentMethodSessionLike {
  client_secret: string | null;
  enabled: boolean;
}

export type PaymentMethodPanel =
  /** Nothing to manage (a Free account has no Stripe customer). */
  | { kind: "hidden" }
  /** The session request is still in flight. */
  | { kind: "loading" }
  /** Mount Stripe's Payment Element with this secret. */
  | { kind: "mount"; clientSecret: string }
  /** A short line explaining why the card form is not on screen. */
  | { kind: "explain"; message: string };

/** This build has no publishable key, so Stripe.js cannot be mounted at all. */
export const NO_PUBLISHABLE_KEY_MESSAGE =
  "Card management isn't available in this build.";

/**
 * Stripe is unconfigured or the account has no customer, yet the account does
 * have a subscription — an ordinary, temporary state, not an error.
 */
export const NO_PAYMENT_METHOD_SESSION_MESSAGE =
  "You'll manage your card here once your account has a payment method on file.";

export interface PaymentMethodPanelInput {
  /** The account has a paid subscription, so there is a card to manage. */
  hasSubscription: boolean;
  /** This build carries a real `pk_` key (`embeddedCheckoutEnabled()`). */
  hasPublishableKey: boolean;
  /** The session response, or `null` while the request is in flight. */
  session: PaymentMethodSessionLike | null;
  /** A message from a failed session request, if any. */
  failure?: string | null;
}

/**
 * Decide what the "Payment method" section renders.
 *
 * `hasSubscription` is checked first so a Free account never sees a card
 * section at all — there is no Stripe customer to manage, and an explanatory
 * line about a card they have never given us would be noise. For a subscriber,
 * a missing key or an `enabled: false` session becomes a short explanatory line
 * rather than an empty Payment Element.
 */
export function paymentMethodPanel(input: PaymentMethodPanelInput): PaymentMethodPanel {
  const { hasSubscription, hasPublishableKey, session, failure } = input;

  if (!hasSubscription) return { kind: "hidden" };

  if (!hasPublishableKey) return { kind: "explain", message: NO_PUBLISHABLE_KEY_MESSAGE };

  const failureText = failure?.trim();
  if (failureText) return { kind: "explain", message: failureText };

  if (session === null) return { kind: "loading" };

  const clientSecret = session.client_secret?.trim();
  if (session.enabled && clientSecret) {
    return { kind: "mount", clientSecret };
  }

  return { kind: "explain", message: NO_PAYMENT_METHOD_SESSION_MESSAGE };
}

/** Whether the section should be rendered at all. */
export function paymentMethodSectionVisible(panel: PaymentMethodPanel): boolean {
  return panel.kind !== "hidden";
}

/* ------------------------------------------------------------------ *
 * Submitting the Payment Element
 * ------------------------------------------------------------------ */

/**
 * The card form never mounted: either Stripe.js was not loaded for this build
 * or the Payment Element is not on screen. Ordinary for a deployment with no
 * publishable key, so it is a line of copy rather than a crash.
 */
export const STRIPE_NOT_READY_MESSAGE = "Card payments are unavailable. Please try again later.";

/**
 * Stripe answered, but the SetupIntent is not `succeeded` — it still needs an
 * action, or was left in a state that did not save a card. Either way there is
 * nothing new to show in the list, so this must not read as success.
 */
export const SETUP_NOT_COMPLETED_MESSAGE = "That card could not be saved. Please try again.";

/** The fallback wording when a failure carries no message of its own. */
export const SAVE_PAYMENT_METHOD_FAILED_MESSAGE = "Could not save that card.";

/**
 * The outcome of one `stripe.confirmSetup` attempt.
 *
 * A discriminated result rather than a thrown error, so the component has one
 * branch (`result.ok`) instead of a try/catch — and so every failure path is a
 * value this module's tests can assert.
 */
export type ConfirmPaymentMethodResult = { ok: true } | { ok: false; message: string };

/**
 * The two Stripe objects the submit path needs, named structurally so a caller
 * can pass the real hooks' return values and a test can pass plain fakes.
 *
 * `Pick<Stripe, "confirmSetup">` (rather than importing a concrete class-ish
 * shape) keeps the overloads Stripe.js actually declares, and makes the real
 * `Stripe | null` from `useStripe` assignable with no cast at the call site.
 */
export interface ConfirmPaymentMethodSetupInput {
  /** From `useStripe()`; `null` until Stripe.js has loaded. */
  stripe: Pick<Stripe, "confirmSetup"> | null | undefined;
  /** From `useElements()`; `null` when no Element is mounted. */
  elements: StripeElements | null | undefined;
  /** Where a redirect-based method returns to; the billing page's own URL. */
  returnUrl: string;
}

/**
 * Confirm the Payment Element's SetupIntent and report what happened.
 *
 * Lifted out of the component because the submit path cannot be exercised in
 * this repo's Node test environment (no jsdom, no real Stripe.js) — the same
 * reason `lib/jobStore` and `lib/stripeCheckout` hold the rules their components
 * only render. The four failure shapes are handled explicitly:
 *
 *  - `stripe`/`elements` missing → the Element never mounted;
 *  - Stripe returning an `error` object (a declined card, a validation fault);
 *  - `confirmSetup` throwing (a network fault, a Stripe.js internal error);
 *  - a returned SetupIntent whose status is not `succeeded`.
 *
 * `redirect: "if_required"` keeps the user on the page for a card: only a
 * redirect-based method sends them to its own authorization screen, and back.
 */
export async function confirmPaymentMethodSetup(
  input: ConfirmPaymentMethodSetupInput,
): Promise<ConfirmPaymentMethodResult> {
  const { stripe, elements, returnUrl } = input;

  if (!stripe || !elements) {
    return { ok: false, message: STRIPE_NOT_READY_MESSAGE };
  }

  try {
    const result = await stripe.confirmSetup({
      elements,
      redirect: "if_required",
      confirmParams: { return_url: returnUrl },
    });

    if (result.error) {
      return { ok: false, message: result.error.message || SAVE_PAYMENT_METHOD_FAILED_MESSAGE };
    }

    if (result.setupIntent?.status !== "succeeded") {
      return { ok: false, message: SETUP_NOT_COMPLETED_MESSAGE };
    }

    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      message:
        err instanceof Error && err.message ? err.message : SAVE_PAYMENT_METHOD_FAILED_MESSAGE,
    };
  }
}
