/**
 * Rules for the in-app, brand-themed checkout.
 *
 * Deliberately pure and free of React: this repo's test environment is Node
 * (no jsdom), so anything that decides *behaviour* has to live outside a
 * component to be testable. The page component keeps only the imperative
 * Stripe.js mounting, which the browser is the only thing able to exercise.
 */

/**
 * Something the checkout page knows how to sell.
 *
 * `promo` is orthogonal to what is being bought, so it is intersected onto the
 * union rather than repeated on each arm. It is absent (not `undefined`) for a
 * URL that carries no `promo` parameter, so every existing call site and test
 * that compares an intent with `toEqual` is unchanged.
 */
export type CheckoutIntent = (
  | { kind: "subscription"; tier: string }
  | { kind: "credits"; amount: number }
) & {
  /**
   * The Stripe promotion code supplied in the URL, in the customer's own
   * casing.
   *
   * Deliberately not upper-cased: Stripe matches codes case-insensitively, and
   * echoing the customer's input back to them is friendlier than shouting it.
   */
  promo?: string;
};

/** Where the branded checkout lives; every URL this module builds targets it. */
export const CHECKOUT_PATH = "/app/checkout";

/**
 * The Stripe publishable key, or `""` when the deployment has not set one.
 *
 * Vite inlines `VITE_*` values at build time, so this is a build-time constant:
 * a deployment built without the key produces a bundle that always asks the API
 * for the hosted page, which is precisely the behaviour that shipped before
 * embedded checkout existed. Merging this feature therefore cannot by itself
 * change how anyone pays.
 */
export function publishableKey(): string {
  const key = import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY as string | undefined;
  return (key ?? "").trim();
}

/**
 * Whether this build can mount an in-page Stripe surface at all.
 *
 * A real publishable key is required. Handing `loadStripe` a missing or
 * malformed key throws deep inside Stripe.js, which would turn a click on
 * "Upgrade" into a blank page — a far worse failure than quietly using the
 * hosted page the app has always used.
 *
 * This gates both in-page surfaces: the checkout page's Payment Element and
 * the Billing page's card form. When it is false the app asks for a **hosted**
 * session and redirects, which is the behaviour that shipped before any of this
 * existed.
 */
export function embeddedCheckoutEnabled(): boolean {
  return publishableKey().startsWith("pk_");
}

/**
 * The `ui_mode` to ask the API for, given what this build can render.
 *
 * `elements`, not `embedded`: embedded Checkout **cannot be themed dark**. Its
 * `branding_settings` covers only background, button, font and shape, Stripe
 * rejects a `theme` parameter outright (verified against the live account),
 * and its payment sheet renders white whatever `background_color` says — the
 * session happily stored `#121417` and still painted a white form.
 *
 * The Payment Element is themed through the Appearance API, which does support
 * a dark theme and is already how the Billing page's card form is styled. Both
 * modes are backed by the *same* Checkout Session, so line items, taxes,
 * metadata and every webhook are unaffected by the choice.
 */
export function requestedUiMode(): "elements" | "hosted" {
  return embeddedCheckoutEnabled() ? "elements" : "hosted";
}

/**
 * Read the purchase this page was opened for out of a query string.
 *
 * Returns `null` for anything unrecognised so the page can say "nothing to
 * check out" rather than creating a session for a bogus value. The tier is
 * *not* checked against the real plan list here: a plan rename must not
 * silently turn a valid upgrade into a dead end, and the API already answers a
 * bad tier with an error the user can act on.
 */
export function parseCheckoutIntent(search: string): CheckoutIntent | null {
  const params = new URLSearchParams(search);
  const promo = normalizePromoCode(params.get("promo"));

  const tier = (params.get("tier") ?? "").trim().toUpperCase();
  if (/^[A-Z][A-Z_]*$/.test(tier)) {
    return promo ? { kind: "subscription", tier, promo } : { kind: "subscription", tier };
  }

  const rawValue = (params.get("credits") ?? "").trim();
  if (rawValue) {
    const amount = Number(rawValue);
    if (Number.isInteger(amount) && amount > 0) {
      return promo ? { kind: "credits", amount, promo } : { kind: "credits", amount };
    }
  }

  return null;
}

/**
 * A promotion code as the customer supplied it, or `undefined` when there is
 * none.
 *
 * Only trimmed and blank-folded — never re-cased (see `CheckoutIntent.promo`).
 * A whitespace-only value is treated as absent so `?promo=` and `?promo=%20`
 * behave exactly like a URL without the parameter.
 */
export function normalizePromoCode(raw: string | null | undefined): string | undefined {
  const value = (raw ?? "").trim();
  return value === "" ? undefined : value;
}

/**
 * The checkout URL with `promo` set to `code`, every other parameter preserved.
 *
 * Why the code travels through the URL instead of a second API call: a Stripe
 * discount is attached to the **session**, so applying a code means creating a
 * new session. Routing it through the URL that already drives session creation
 * keeps exactly one code path and leaves validation to the server, which is the
 * only party that can validate it. Doing it on submit (not on every keystroke)
 * also avoids minting a session per character.
 *
 * `encodeURIComponent` rather than `URLSearchParams.set` so a code containing
 * spaces or reserved characters cannot break the query string.
 */
export function checkoutUrlWithPromo(search: string, code: string): string {
  const params = new URLSearchParams(search);
  params.delete("promo");
  const base = params.toString();
  const promo = `promo=${encodeURIComponent(code.trim())}`;
  return `${CHECKOUT_PATH}${base ? `?${base}&` : "?"}${promo}`;
}

/**
 * The checkout URL with `promo` dropped and everything else kept.
 *
 * This is the escape hatch for a mistyped or expired code: one click returns
 * the customer to an ordinary checkout instead of stranding them on an error
 * they cannot clear.
 */
export function checkoutUrlWithoutPromo(search: string): string {
  const params = new URLSearchParams(search);
  params.delete("promo");
  const base = params.toString();
  return base ? `${CHECKOUT_PATH}?${base}` : CHECKOUT_PATH;
}

/**
 * The primary button's wording for the amount the session will charge.
 *
 * Only `0` means free: the server asks Stripe for
 * `payment_method_collection: "if_required"`, so a zero-total session genuinely
 * collects no card and "Pay" would be a lie. Anything else — including a
 * `null`/absent total from an API older than this field — is the ordinary
 * paying path. That default is deliberate: it is far better to show "Pay" for
 * a purchase that turns out to be free than to promise a free month the
 * customer is then billed for.
 */
export function checkoutButtonLabel(amountTotal: number | null | undefined): string {
  return amountTotal === 0 ? "Start my free month" : "Pay";
}

/** The line under the button, matched to whether a card will be collected. */
export function checkoutCardHint(amountTotal: number | null | undefined): string {
  return amountTotal === 0
    ? "No card required — nothing is due today."
    : "Payments are handled by Stripe. Your card details never touch our servers.";
}

/**
 * A human label for what is being bought, for the page heading and the
 * screen-reader announcement.
 */
export function describeIntent(intent: CheckoutIntent): string {
  return intent.kind === "subscription"
    ? `the ${intent.tier.replace(/_/g, " ")} plan`
    : `${intent.amount} conversion credits`;
}
