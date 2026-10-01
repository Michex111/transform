/**
 * Whether — and how — the Billing page can show in-app card management.
 *
 * The Customer Portal's card screen cannot be branded, so the page mounts
 * Stripe's Payment Element against a Customer Session instead. Whether that is
 * possible depends on things only the API and the build know, and getting it
 * wrong means a blank iframe or a button that throws — so the decision lives
 * here, pure and unit-tested, and the component only renders the result.
 *
 * Deliberately free of React and of Stripe.js: this repo's test environment is
 * Node with no jsdom, so importing either would make the rules untestable.
 */

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
