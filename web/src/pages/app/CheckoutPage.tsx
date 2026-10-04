/**
 * The brand-themed checkout.
 *
 * Replaces the redirect to `checkout.stripe.com` with Stripe's **Payment
 * Element** mounted inside our own dark page. Stripe still hosts and renders the
 * card fields inside an iframe, so the PCI scope is unchanged (SAQ A) — only the
 * chrome around it is ours, and the form is themed with the Appearance API.
 *
 * Why the Payment Element rather than Stripe's embedded Checkout, which this
 * page used before: embedded Checkout **cannot be made dark**. Its
 * `branding_settings` exposes only background, button, font and shape; Stripe
 * rejects a `theme`/`color_mode` parameter outright, and the payment sheet
 * renders white no matter what `background_color` says — the session stored
 * `#121417` and still painted a white form (both verified against the live
 * account, and visible in the rendered `—checkout-white: #ffffff` on the
 * sheet). The Payment Element is themed through the Appearance API, which does
 * support a dark theme and is already how the Billing page's card form looks.
 *
 * Crucially this is **not** a change of payment product: the server still
 * creates a Checkout Session (`ui_mode: "elements"`), so line items, taxes,
 * metadata, fulfilment and every webhook behave exactly as before.
 *
 * What is being bought is read from the query string rather than from router
 * state, so a refresh or a bookmark keeps working. The session is created by
 * this page on mount: a checkout client secret is short-lived and single-use, so
 * passing one through navigation would leave a stale secret behind the back
 * button.
 */

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import {
  CheckoutElementsProvider,
  PaymentElement,
  useCheckoutElements,
} from "@stripe/react-stripe-js/checkout";
import { Check, Lock } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Badge, Button, Card, Logo, Skeleton } from "@/components/ui";
import { trustedExternalUrl } from "@/lib/download";
import { discountSummary } from "@/lib/promoDiscount";
import { STRIPE_CHECKOUT_APPEARANCE } from "@/lib/stripeAppearance";
import {
  checkoutButtonLabel,
  checkoutCardHint,
  checkoutUrlWithPromo,
  checkoutUrlWithoutPromo,
  describeIntent,
  embeddedCheckoutEnabled,
  normalizePromoCode,
  parseCheckoutIntent,
  publishableKey,
  requestedUiMode,
} from "@/lib/stripeCheckout";
import type { CheckoutResponse } from "@/api/types";

/**
 * Stripe.js is loaded once per page life. `loadStripe` injects a `<script>` tag
 * on every call, so calling it per mount would add a duplicate on every
 * StrictMode pass and every hot reload.
 *
 * Evaluated at module scope, and only when a key exists: this module is a lazy
 * chunk, so the script is fetched on the first visit to checkout and never for
 * anyone else. Handing `loadStripe` an empty string would throw here, at import
 * time, and take the whole route down with it.
 */
const stripePromise: Promise<Stripe | null> | null = embeddedCheckoutEnabled()
  ? loadStripe(publishableKey())
  : null;

/** What the customer is buying, once the pricing endpoints have answered. */
interface Summary {
  title: string;
  /** Formatted total, or `null` when the API did not quote one. */
  amount: string | null;
  lines: string[];
}

type Phase = "creating" | "ready" | "failed";

/**
 * The form, which must sit inside `<CheckoutElementsProvider>`: the checkout
 * hooks only work there.
 *
 * `checkout.confirm()` with no options uses the default `redirect: "always"`,
 * which lands the customer on the session's `return_url` — the same URL the
 * hosted flow used, so the SPA's existing `?checkout=success` handling (and the
 * webhook that actually grants the credits or activates the plan) fires exactly
 * as before. `if_required` would keep a card on the page but leave the success
 * destination to be re-derived here, duplicating server config for no gain.
 */
function CheckoutForm({
  amountTotal,
  onFailure,
}: {
  /** Minor-unit total the session will charge, or `null` when unreported. */
  amountTotal: number | null;
  onFailure: (message: string) => void;
}) {
  const state = useCheckoutElements();
  const [submitting, setSubmitting] = useState(false);

  if (state.type === "loading") return <FormSkeleton />;

  // Stripe reports a rejected session (an expired secret, a misconfiguration)
  // as this state rather than by throwing — but it can also throw, which is
  // what the boundary around this tree is for.
  if (state.type === "error") {
    return <FormUnavailable message={state.error.message} />;
  }

  const { checkout } = state;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    const result = await checkout.confirm();
    if (result.type === "error") {
      setSubmitting(false);
      onFailure(result.error.message);
      return;
    }
    // Success means the redirect to `return_url` is under way; leaving the
    // button in its busy state is deliberate, since the page is on its way out.
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5 p-6">
      <PaymentElement />
      <Button type="submit" size="lg" disabled={submitting}>
        {submitting ? "Processing…" : checkoutButtonLabel(amountTotal)}
      </Button>
      <p className="flex items-center justify-center gap-1.5 text-xs text-muted">
        <Lock size={13} weight="fill" aria-hidden />
        {checkoutCardHint(amountTotal)}
      </p>
    </form>
  );
}

/** The form's own placeholder, so the page keeps its shape while it loads. */
function FormSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-6" role="status" aria-live="polite">
      <Skeleton className="h-4 w-28" />
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-full" />
      <Skeleton className="h-11 w-2/3" />
      <Skeleton className="mt-1 h-12 w-full" />
      <span className="text-sm text-muted">Preparing secure checkout…</span>
    </div>
  );
}

/**
 * Shown when the form itself cannot be used.
 *
 * Both paths that reach this are honest about it and leave a way forward: the
 * customer can go back to billing (where the card forms and the hosted portal
 * live) or pick a plan again. Never a dead end and never a blank card.
 */
function FormUnavailable({ message }: { message: string }) {
  return (
    <div className="p-6" role="alert">
      <p className="text-sm font-medium text-on-background">Payment couldn't be loaded</p>
      <p className="mt-1.5 text-sm text-muted">{message}</p>
      <div className="mt-5 flex flex-wrap gap-3">
        <Link
          to="/app/billing"
          className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-semibold text-on-primary transition-colors hover:bg-primary/90"
        >
          Back to billing
        </Link>
      </div>
    </div>
  );
}

export function CheckoutPage() {
  const { api: client } = useAuth();
  const [searchParams] = useSearchParams();
  const search = searchParams.toString();
  const navigate = useNavigate();

  // Keyed on the search string, not on `searchParams`, which is a fresh object
  // on every render and would re-create the session in a loop.
  const intent = useMemo(() => parseCheckoutIntent(search), [search]);

  const [phase, setPhase] = useState<Phase>("creating");
  const [failure, setFailure] = useState("");
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  // The session's own report of what was discounted. `null` until the API
  // answers, and on an API too old to send the additive discount fields.
  const [session, setSession] = useState<CheckoutResponse | null>(null);
  // The code as the customer is typing it; only read on submit.
  const [promoDraft, setPromoDraft] = useState("");

  // Everything the order summary and the button say about the discount is
  // derived here, from pure helpers, so none of it is decided inline in JSX.
  const discount = useMemo(
    () => discountSummary(session, { renewalPrice: summary?.amount ?? null }),
    [session, summary],
  );
  const amountTotal = session?.amount_total ?? null;
  const isSubscription = intent?.kind === "subscription";

  // A description of the purchase, fetched independently of the payment flow so
  // a failure to load it can never block or delay checkout. The form is the
  // product here; the summary is a courtesy.
  useEffect(() => {
    if (!intent) return;
    let active = true;

    const request =
      intent.kind === "credits"
        ? client.creditPricing().then((pricing) => {
            const pack = pricing.find((p) => p.credits === intent.amount);
            return {
              title: `${intent.amount} conversion credits`,
              amount: pack ? `$${pack.price_usd.toFixed(2)}` : null,
              lines: pack ? [`$${pack.price_per_credit.toFixed(2)} per credit`] : [],
            };
          })
        : client.subscriptionPlans().then((plans) => {
            const plan = plans.find((p) => p.tier === intent.tier);
            return {
              title: plan?.name ?? intent.tier.replace(/_/g, " "),
              amount: plan?.price_monthly_usd != null ? `$${plan.price_monthly_usd}` : null,
              lines: plan?.features.slice(0, 4) ?? [],
            };
          });

    request
      .then((next) => {
        if (active) setSummary(next);
      })
      .catch(() => {
        // Silent: the summary is decoration, and a toast about it would sit on
        // top of the payment form.
        if (active) setSummary(null);
      });

    return () => {
      active = false;
    };
  }, [client, intent]);

  useEffect(() => {
    if (!intent) {
      setPhase("failed");
      setFailure("There is nothing to check out. Pick a plan to get started.");
      return;
    }

    let cancelled = false;

    void (async () => {
      try {
        // Only ever ask for `elements` when this build can actually mount it.
        // A build with no publishable key asks for nothing and receives a
        // hosted URL, i.e. exactly the pre-existing behaviour.
        const mode = requestedUiMode() === "elements" ? ("elements" as const) : undefined;

        const handle =
          intent.kind === "subscription"
            ? await client.checkout(intent.tier, mode, intent.promo)
            : await client.purchaseCredits(intent.amount, mode);

        if (cancelled) return;

        // The session is the only thing that knows the real total and whether a
        // code was applied, so both the summary and the button read from it.
        setSession(handle);

        if (!handle.client_secret) {
          // Hosted fallback. Guarded the same way downloads are: assigning an
          // API-supplied `javascript:` URL to `location` would run in this
          // origin, and an arbitrary host would be an open redirect.
          const target = trustedExternalUrl(handle.checkout_url);
          if (!target) throw new Error("The checkout link was not valid. Please try again.");
          window.location.assign(target);
          return;
        }

        // The secret goes straight to the provider; the Element is mounted by
        // React rather than imperatively, so there is no instance to tear down
        // and no StrictMode double-mount race to manage.
        setClientSecret(handle.client_secret);
        setPhase("ready");
      } catch (err) {
        if (cancelled) return;
        setPhase("failed");
        setFailure(
          err instanceof Error ? err.message : "Could not start checkout. Please try again.",
        );
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [client, intent]);

  // A failed confirmation keeps the session usable, so this reports the problem
  // without discarding the form the customer has already filled in.
  function reportFailure(message: string) {
    setFailure(message);
  }

  /**
   * Apply a typed code by navigating to the same checkout URL with `&promo=`.
   *
   * A Stripe discount is attached to the **session**, so applying a code means
   * creating a new session — and the URL is what drives session creation here.
   * Routing the code through it keeps exactly ONE code path and leaves
   * validation to the server. This runs on submit rather than on every
   * keystroke, which also avoids minting a session per character.
   */
  function applyPromo(event: FormEvent) {
    event.preventDefault();
    const code = normalizePromoCode(promoDraft);
    if (!code) return;
    navigate(checkoutUrlWithPromo(search, code), { replace: true });
  }

  /** Return to an ordinary checkout when a code was mistyped or has expired. */
  function removePromo() {
    navigate(checkoutUrlWithoutPromo(search), { replace: true });
  }

  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b border-outline">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-5 py-4">
          <Logo />
          <Link
            to="/app/billing"
            className="rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-variant hover:text-on-background"
          >
            Cancel
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-5 py-10">
        <h1 className="font-display text-2xl font-semibold tracking-tight">
          Complete your purchase
        </h1>
        <p className="mt-1.5 text-sm text-muted">
          {/* Naming the purchase in the heading matters for a screen reader:
              the summary card is the only other place it appears, and that
              loads after the form. */}
          {intent ? `You're buying ${describeIntent(intent)}. ` : ""}
          Payment is handled by Stripe — your card details never touch our servers.
        </p>

        <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0">
            {phase === "failed" ? (
              <Card className="p-6">
                <p className="text-sm font-medium text-on-background">
                  Checkout could not be started
                </p>
                <p className="mt-1.5 text-sm text-muted">{failure}</p>
                <div className="mt-5 flex flex-wrap gap-3">
                  {/* The retry-without-the-code path. A mistyped or expired code
                      must never leave the customer on a dead page. */}
                  {intent?.promo && (
                    <Button type="button" onClick={removePromo}>
                      Remove promo code
                    </Button>
                  )}
                  <Link
                    to="/app/billing"
                    className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-semibold text-on-primary transition-colors hover:bg-primary/90"
                  >
                    Back to billing
                  </Link>
                  <Link
                    to="/pricing"
                    className="inline-flex h-10 items-center rounded-lg border border-outline-strong px-4 text-sm font-semibold text-on-background transition-colors hover:bg-surface-variant"
                  >
                    See plans
                  </Link>
                </div>
              </Card>
            ) : (
              <>
                <Card className="overflow-hidden">
                  {phase === "creating" || !clientSecret ? (
                    <FormSkeleton />
                  ) : (
                    // Stripe.js throws on an unusable secret — inside render,
                    // which without a boundary unmounts this whole route and
                    // leaves a blank page (verified in a browser on the Billing
                    // card form). The boundary keeps the failure inside the
                    // form, and `resetKey` lets a retry clear it.
                    <ErrorBoundary
                      resetKey={clientSecret}
                      onError={(err) => reportFailure(err.message)}
                      fallback={<FormUnavailable message={failure || "Please try again."} />}
                    >
                      <CheckoutElementsProvider
                        stripe={stripePromise}
                        options={{
                          clientSecret,
                          // Branded, not Stripe's stock light theme. The
                          // Appearance API takes literal values because its
                          // iframe cannot resolve our CSS custom properties,
                          // which is why the palette lives as hex in exactly one
                          // place — `lib/stripeAppearance` — pinned to `@theme`
                          // by a test.
                          elementsOptions: { appearance: STRIPE_CHECKOUT_APPEARANCE },
                        }}
                      >
                        <CheckoutForm amountTotal={amountTotal} onFailure={reportFailure} />
                      </CheckoutElementsProvider>
                    </ErrorBoundary>
                  )}
                </Card>

                {/* A confirmation failure is reported here rather than inside
                    the form, so the card the customer already filled in is not
                    thrown away. */}
                {phase === "ready" && failure && (
                  <p
                    role="alert"
                    className="mt-4 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-error"
                  >
                    {failure}
                  </p>
                )}

                <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted">
                  <Lock size={13} weight="fill" aria-hidden />
                  Secured by Stripe
                </p>
              </>
            )}
          </div>

          <aside aria-label="Order summary">
            <Card className="p-6">
              {summary ? (
                <>
                  <p className="text-xs font-medium uppercase tracking-wider text-muted">Summary</p>
                  <div className="mt-3 flex items-baseline justify-between gap-4">
                    <span className="font-display text-base font-semibold">{summary.title}</span>
                    {discount?.free ? (
                      // A struck-through original next to an explicit free
                      // statement: the customer must never read the crossed-out
                      // price as a charge.
                      <span className="flex flex-col items-end text-right">
                        {summary.amount && (
                          <span className="text-sm text-muted line-through">{summary.amount}</span>
                        )}
                        <span className="font-display text-lg font-semibold text-success">
                          {discount.headline}
                        </span>
                      </span>
                    ) : discount?.total ? (
                      <span className="flex flex-col items-end text-right">
                        {summary.amount && (
                          <span className="text-sm text-muted line-through">{summary.amount}</span>
                        )}
                        <span className="font-display text-lg font-semibold">{discount.total}</span>
                      </span>
                    ) : (
                      summary.amount && (
                        <span className="font-display text-lg font-semibold">{summary.amount}</span>
                      )
                    )}
                  </div>
                  {summary.lines.length > 0 && (
                    <ul className="mt-4 space-y-2 border-t border-outline pt-4">
                      {summary.lines.map((line) => (
                        <li
                          key={line}
                          className="flex items-start gap-2 text-sm text-on-background"
                        >
                          <Check size={15} weight="bold" className="mt-0.5 shrink-0 text-success" />
                          {line}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <div className="space-y-3">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-5 w-40" />
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-4/5" />
                </div>
              )}

              {/* Rendered outside the summary block: the discount is a fact
                  from the session, not from the pricing catalogue, and must
                  still appear if the courtesy summary failed to load. */}
              {discount && (
                <div className="mt-4 rounded-lg border border-outline bg-success/10 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-semibold text-success">{discount.headline}</p>
                    {discount.code && <Badge color="var(--color-success)">{discount.code}</Badge>}
                  </div>
                  {discount.dueToday && (
                    <p className="mt-1.5 text-sm font-medium text-on-background">
                      {discount.dueToday}
                    </p>
                  )}
                  {discount.detail && <p className="mt-1.5 text-xs text-muted">{discount.detail}</p>}
                </div>
              )}

              {/* The code is in the URL but the session has not answered yet.
                  Without this the summary would look identical to a plain
                  checkout while the discount is being applied. */}
              {isSubscription && intent?.promo && !discount && (
                <p className="mt-4 border-t border-outline pt-4 text-xs text-muted">
                  Applying code <span className="font-medium text-on-background">{intent.promo}</span>…
                </p>
              )}

              {/* Entry point for a code the customer was given out of band. It
                  only exists before a code is applied; once one is in the URL
                  the discount block above (or the error below) takes over. */}
              {isSubscription && !intent?.promo && (
                <details className="mt-4 border-t border-outline pt-4">
                  <summary className="cursor-pointer text-sm text-muted transition-colors hover:text-on-background">
                    Have a promo code?
                  </summary>
                  <form onSubmit={applyPromo} className="mt-3 flex gap-2">
                    <input
                      type="text"
                      value={promoDraft}
                      onChange={(event) => setPromoDraft(event.target.value)}
                      placeholder="Promo code"
                      aria-label="Promo code"
                      autoComplete="off"
                      className="h-10 min-w-0 flex-1 rounded-lg border border-outline-strong bg-surface-variant px-3 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none"
                    />
                    <Button
                      type="submit"
                      variant="secondary"
                      size="sm"
                      disabled={!promoDraft.trim()}
                    >
                      Apply
                    </Button>
                  </form>
                </details>
              )}

              {isSubscription && (
                <p className="mt-4 border-t border-outline pt-4 text-xs text-muted">
                  Billed monthly. Cancel any time — you keep your plan until the period ends.
                </p>
              )}
            </Card>
          </aside>
        </div>
      </main>
    </div>
  );
}
