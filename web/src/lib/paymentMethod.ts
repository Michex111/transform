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
 * rules untestable. The Stripe.js references below are all `import type`, which
 * is erased at build time — the submit logic they type is exercised with plain
 * object fakes, not a DOM or a real Stripe.js instance.
 *
 * Four groups live here:
 *  - {@link paymentMethodPanel}: what the section renders at all (hidden,
 *    loading, mounted, or one explanatory line);
 *  - the card presentation rules ({@link cardBrandLabel}, {@link cardExpiry},
 *    {@link addCardPrompt}, …): what each saved card says, and whether it needs
 *    attention before a renewal fails;
 *  - the Elements options builders ({@link paymentElementOptions},
 *    {@link paymentElementElementOptions}) — notably the either/or between a
 *    server-created SetupIntent and the deferred mode, which Stripe rejects as
 *    an integration error if both are passed;
 *  - {@link confirmPaymentMethodSetup}, the extracted submit path.
 */

import type {
  Stripe,
  StripeElements,
  StripeElementsOptions,
  StripePaymentElementOptions,
} from "@stripe/stripe-js";

import { STRIPE_CHECKOUT_APPEARANCE } from "@/lib/stripeAppearance";

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

/**
 * Whether the section must fall back to Stripe's portal to add a card.
 *
 * True only for the "this build has no publishable key" case: the app can still
 * list, default and remove cards, but it cannot mount the Payment Element, so
 * without this the customer would have no way to add one at all. The other
 * explanatory states mean there is genuinely nothing to manage (no customer
 * yet, or a request that failed), where the portal would be a dead end too.
 */
export function needsPortalFallback(panel: PaymentMethodPanel): boolean {
  return panel.kind === "explain" && panel.message === NO_PUBLISHABLE_KEY_MESSAGE;
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

/* ------------------------------------------------------------------ *
 * Presenting a saved card
 * ------------------------------------------------------------------ */

/**
 * Stripe's brand codes mapped to the name a cardholder would recognise.
 *
 * A miss falls back to title-casing the code rather than printing it raw, so a
 * brand Stripe adds later reads as "Cartes Bancaires" instead of
 * "cartes_bancaires" without needing a release.
 */
const BRAND_LABELS: Record<string, string> = {
  visa: "Visa",
  mastercard: "Mastercard",
  amex: "American Express",
  american_express: "American Express",
  discover: "Discover",
  diners: "Diners Club",
  diners_club: "Diners Club",
  jcb: "JCB",
  unionpay: "UnionPay",
  maestro: "Maestro",
  cartes_bancaires: "Cartes Bancaires",
  eftpos_au: "eftpos",
  link: "Link",
  unknown: "Card",
};

function titleCaseCode(value: string): string {
  return value.replace(/_/g, " ").replace(/\b[a-z]/g, (c) => c.toUpperCase());
}

/** `"visa"` → `"Visa"`; `"unknown"`/`""` → `"Card"`. */
export function cardBrandLabel(brand: string | null | undefined): string {
  const key = (brand ?? "").trim().toLowerCase();
  if (!key) return "Card";
  return BRAND_LABELS[key] ?? titleCaseCode(key);
}

const WALLET_LABELS: Record<string, string> = {
  apple_pay: "Apple Pay",
  google_pay: "Google Pay",
  link: "Link",
};

/** `"apple_pay"` → `"Apple Pay"`; anything empty/unrecognised → `null`. */
export function walletLabel(wallet: string | null | undefined): string | null {
  const key = (wallet ?? "").trim().toLowerCase();
  if (!key) return null;
  return WALLET_LABELS[key] ?? titleCaseCode(key);
}

/** `"Visa •••• 4242"`, or just the brand when there is no last4 to show. */
export function cardTitle(card: {
  brand: string;
  last4: string;
}): string {
  const brand = cardBrandLabel(card.brand);
  return card.last4 ? `${brand} •••• ${card.last4}` : brand;
}

export type CardExpiryState = "ok" | "expiring" | "expired";

export interface CardExpiry {
  state: CardExpiryState;
  /** `"12/2030"`, or `""` when the API sent no usable expiry. */
  label: string;
  /** Whole days until the card lapses; negative once past. `null` when unknown. */
  daysLeft: number | null;
}

/**
 * How long before a card lapses to start warning about it.
 *
 * Two months: long enough that the customer can act on it well before a renewal
 * fails, short enough that the warning means something when it appears.
 */
export const CARD_EXPIRY_WARNING_DAYS = 60;

/**
 * A card's expiry and whether it needs attention.
 *
 * A card is valid through the **end** of its expiry month, so it lapses at the
 * first instant of the following month — an `01/2027` card is still good in
 * January 2027. Treating the month itself as the deadline would flag a usable
 * card as expired for up to 31 days.
 *
 * An unusable `exp_month`/`exp_year` (a malformed payload normalises to `0`)
 * yields an empty label with state `"ok"`: the caller omits the line rather than
 * printing "Expires 00/0000" or, worse, claiming the card has expired.
 */
export function cardExpiry(
  card: { exp_month: number; exp_year: number },
  now: Date = new Date(),
): CardExpiry {
  const month = Math.trunc(card.exp_month);
  const year = Math.trunc(card.exp_year);
  if (!Number.isFinite(month) || !Number.isFinite(year) || month < 1 || month > 12 || year < 2000) {
    return { state: "ok", label: "", daysLeft: null };
  }

  const label = `${String(month).padStart(2, "0")}/${year}`;
  // `month` is 1-based and `Date.UTC`'s month is 0-based, so passing `month`
  // directly is exactly "the first instant after the expiry month".
  const lapsesAt = Date.UTC(year, month, 1);
  const daysLeft = Math.floor((lapsesAt - now.getTime()) / 86_400_000);

  if (daysLeft <= 0) return { state: "expired", label, daysLeft };
  if (daysLeft <= CARD_EXPIRY_WARNING_DAYS) return { state: "expiring", label, daysLeft };
  return { state: "ok", label, daysLeft };
}

/** The card flagged as the default, or `null` when none is. */
export function defaultCard<T extends { is_default: boolean }>(cards: readonly T[]): T | null {
  return cards.find((card) => card.is_default) ?? null;
}

/**
 * The nudge shown under the card list, or `null` when there is nothing to say.
 *
 * Both sentences state a real consequence rather than an upsell: with no card
 * on file a paid plan cannot renew at all, and with exactly one card a single
 * decline or expiry interrupts the subscription.
 */
export function addCardPrompt(cards: readonly unknown[]): string | null {
  if (cards.length === 0) return "Add a card so your plan can renew without interruption.";
  if (cards.length === 1) {
    return "Add a backup card so an expired or declined card doesn't interrupt your plan.";
  }
  return null;
}

/** Prefill for the card form, taken from the signed-in account. */
export interface BillingDefaults {
  name?: string;
  email?: string;
}

/**
 * The billing details to prefill the Payment Element with.
 *
 * Only ever the account's own values, and only when there is something to put
 * in the field: an absent key leaves Stripe's own empty input rather than
 * passing `""`, which would suppress the placeholder. A full name is preferred
 * over `display_name` because the latter can be the username.
 */
export function billingDefaults(
  user:
    | {
        first_name?: string | null;
        last_name?: string | null;
        display_name?: string | null;
        email?: string | null;
      }
    | null
    | undefined,
): BillingDefaults {
  const defaults: BillingDefaults = {};

  const fullName = [user?.first_name, user?.last_name]
    .map((part) => part?.trim() ?? "")
    .filter(Boolean)
    .join(" ");
  const name = fullName || (user?.display_name ?? "").trim();
  if (name) defaults.name = name;

  const email = (user?.email ?? "").trim();
  if (email) defaults.email = email;

  return defaults;
}

/* ------------------------------------------------------------------ *
 * Mounting the Payment Element for a new card
 * ------------------------------------------------------------------ */

/**
 * Whether a string is shaped like a Stripe client secret.
 *
 * Stripe ids are `<prefix>_<random>`, and a client secret is
 * `<that id>_secret_<random>` — so the part before `_secret_` must itself
 * contain an underscore. That is what separates a real secret
 * (`seti_1ABC_secret_xyz`) from a truncated or placeholder one
 * (`seti_secret_1`).
 *
 * This matters because Stripe does not *report* a malformed secret, it
 * **throws** — inside the render of `Elements`, which unmounts the whole tree
 * it is rendered in. On the Billing page that means a blank screen where the
 * customer was trying to fix their payment. Verified in a browser: passing an
 * unsplit secret produced `IntegrationError: clientSecret should be a client
 * secret of the form ${id}_secret_${secret}` and an empty page.
 *
 * Deliberately NOT applied to a Customer Session secret: that one is opaque
 * and does not use this shape at all (Stripe's own docs show
 * `_POpxYpmkXdtttYtZQYhrsOJZ2RCQ9kCqqXRU6qrP5c4Jgje`). Only the intent secret
 * has a checkable form, so only that one is checked here; the error boundary
 * around the Element is the backstop for the rest.
 */
export function looksLikeClientSecret(value: string | null | undefined): boolean {
  const secret = (value ?? "").trim();
  if (!secret) return false;
  const [id, random] = secret.split("_secret_");
  return Boolean(id && random && id.includes("_"));
}

/**
 * Whether the form can be mounted with a **server-created SetupIntent**.
 *
 * `clientSecret` and `mode` are mutually exclusive on the Elements options —
 * passing both is an integration error rather than a preference — so this
 * decision is made in exactly one place. A blank or malformed secret (an older
 * API, a placeholder, or a SetupIntent Stripe refused to create) falls back to
 * the deferred mode, which is what every deployment without the endpoint uses.
 */
export type PaymentElementMount =
  | { kind: "setup-intent"; clientSecret: string }
  | { kind: "deferred" };

export function paymentElementMount(
  setupIntentClientSecret: string | null | undefined,
): PaymentElementMount {
  const secret = (setupIntentClientSecret ?? "").trim();
  if (!looksLikeClientSecret(secret)) return { kind: "deferred" };
  return { kind: "setup-intent", clientSecret: secret };
}

/**
 * The `Elements` options for adding a card.
 *
 * `customerSessionClientSecret` is always present — it is what lets Stripe show
 * the customer's saved methods and offer to save a new one. The intent itself
 * comes either from the server (`clientSecret`) or from Stripe at confirmation
 * time (`mode: "setup"` + the currency every price in this product is quoted
 * in); see {@link paymentElementMount}.
 */
export function paymentElementOptions(
  customerSessionClientSecret: string,
  setupIntentClientSecret: string | null | undefined,
): StripeElementsOptions {
  const shared = {
    customerSessionClientSecret,
    appearance: STRIPE_CHECKOUT_APPEARANCE,
  };
  const mount = paymentElementMount(setupIntentClientSecret);
  return mount.kind === "setup-intent"
    ? { ...shared, clientSecret: mount.clientSecret }
    : { ...shared, mode: "setup", currency: "usd" };
}

/**
 * Options for the Payment Element itself.
 *
 * `defaultValues` prefills the cardholder name and email from the account, so
 * the fields the customer has already told us are not asked for twice.
 * `wallets: "auto"` keeps Apple Pay and Google Pay available when the browser
 * and the Stripe account support them; it is their default, and stating it
 * documents that it is deliberate.
 */
export function paymentElementElementOptions(
  defaults: BillingDefaults,
): StripePaymentElementOptions {
  const billingDetails: { name?: string; email?: string } = {};
  if (defaults.name) billingDetails.name = defaults.name;
  if (defaults.email) billingDetails.email = defaults.email;

  return {
    layout: "accordion",
    wallets: { applePay: "auto", googlePay: "auto" },
    ...(Object.keys(billingDetails).length > 0 ? { defaultValues: { billingDetails } } : {}),
  };
}

/** What to say when a card was saved but the page is about to re-read the list. */
export const PAYMENT_METHOD_SAVED_MESSAGE = "Payment method saved";

/**
 * Shown in place of the card form when Stripe.js refused to mount it.
 *
 * Reaching this means the element threw while rendering, which without the
 * boundary would have taken the whole Billing page down with it.
 */
export const CARD_FORM_FAILED_MESSAGE =
  "The card form couldn't be loaded. You can still add or change a card in the billing portal.";
