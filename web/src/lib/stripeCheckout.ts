/**
 * Rules for the in-app, brand-themed checkout.
 *
 * Deliberately pure and free of React: this repo's test environment is Node
 * (no jsdom), so anything that decides *behaviour* has to live outside a
 * component to be testable. The page component keeps only the imperative
 * Stripe.js mounting, which the browser is the only thing able to exercise.
 */

/** Something the checkout page knows how to sell. */
export type CheckoutIntent =
  | { kind: "subscription"; tier: string }
  | { kind: "credits"; amount: number };

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

  const tier = (params.get("tier") ?? "").trim().toUpperCase();
  if (/^[A-Z][A-Z_]*$/.test(tier)) return { kind: "subscription", tier };

  const rawValue = (params.get("credits") ?? "").trim();
  if (rawValue) {
    const amount = Number(rawValue);
    if (Number.isInteger(amount) && amount > 0) return { kind: "credits", amount };
  }

  return null;
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
