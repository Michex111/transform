/**
 * The brand-themed checkout.
 *
 * Replaces the redirect to `checkout.stripe.com` with Stripe's **embedded**
 * checkout mounted inside our own page, which is what lets the surrounding
 * frame carry the product's navigation, typography and colour. Stripe still
 * hosts and renders the card fields inside an iframe, so the PCI scope is
 * unchanged (SAQ A) — only the chrome around it is ours.
 *
 * What is being bought is read from the query string rather than from router
 * state, so a refresh or a bookmark keeps working. The session is created by
 * this page on mount: an embedded session's client secret is short-lived and
 * single-use, so passing one through navigation would leave a stale secret
 * behind the back button.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { loadStripe, type Stripe, type StripeEmbeddedCheckout } from "@stripe/stripe-js";
import { Check, Lock } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { Card, Logo, Skeleton } from "@/components/ui";
import { trustedExternalUrl } from "@/lib/download";
import {
  describeIntent,
  embeddedCheckoutEnabled,
  parseCheckoutIntent,
  publishableKey,
  requestedUiMode,
} from "@/lib/stripeCheckout";

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

export function CheckoutPage() {
  const { api: client } = useAuth();
  const [searchParams] = useSearchParams();
  const search = searchParams.toString();

  // Keyed on the search string, not on `searchParams`, which is a fresh object
  // on every render and would re-create the session in a loop.
  const intent = useMemo(() => parseCheckoutIntent(search), [search]);

  const slotRef = useRef<HTMLDivElement | null>(null);
  const [phase, setPhase] = useState<Phase>("creating");
  const [failure, setFailure] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);

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
    let instance: StripeEmbeddedCheckout | null = null;

    void (async () => {
      try {
        // Only ever ask for `embedded` when this build can actually render it.
        // A build with no publishable key asks for nothing and receives a
        // hosted URL, i.e. exactly the pre-existing behaviour.
        const mode = requestedUiMode() === "embedded" ? ("embedded" as const) : undefined;

        const handle =
          intent.kind === "subscription"
            ? await client.checkout(intent.tier, mode)
            : await client.purchaseCredits(intent.amount, mode);

        if (cancelled) return;

        if (!handle.client_secret) {
          // Hosted fallback. Guarded the same way downloads are: assigning an
          // API-supplied `javascript:` URL to `location` would run in this
          // origin, and an arbitrary host would be an open redirect.
          const target = trustedExternalUrl(handle.checkout_url);
          if (!target) throw new Error("The checkout link was not valid. Please try again.");
          window.location.assign(target);
          return;
        }

        const stripe = await stripePromise;
        if (cancelled) return;
        if (!stripe) throw new Error("Card payments are unavailable. Please try again later.");

        const checkout = await stripe.createEmbeddedCheckoutPage({
          clientSecret: handle.client_secret,
        });

        if (cancelled) {
          // StrictMode ran this effect twice and this instance lost the race.
          // Destroying it here is what keeps an orphaned iframe out of the DOM
          // (and `instance` stays null, so cleanup cannot double-destroy it).
          checkout.destroy();
          return;
        }

        instance = checkout;
        if (slotRef.current) checkout.mount(slotRef.current);
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
      instance?.destroy();
    };
  }, [client, intent]);

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
              <Card className="relative min-h-[22rem] overflow-hidden">
                {/* Stripe mounts its iframe into this node. It is rendered in
                    both the "creating" and "ready" phases and is never swapped
                    for a different element, so React cannot replace the node
                    out from under the mounted iframe. */}
                <div ref={slotRef} className="min-h-[22rem]" />
                {phase === "creating" && (
                  <div
                    className="absolute inset-0 flex flex-col gap-3 bg-surface p-6"
                    role="status"
                    aria-live="polite"
                  >
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-11 w-full" />
                    <Skeleton className="h-11 w-full" />
                    <Skeleton className="h-11 w-2/3" />
                    <span className="mt-2 text-sm text-muted">Preparing secure checkout…</span>
                  </div>
                )}
              </Card>
            )}

            {phase !== "failed" && (
              <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted">
                <Lock size={13} weight="fill" aria-hidden />
                Secured by Stripe
              </p>
            )}
          </div>

          <aside aria-label="Order summary">
            <Card className="p-6">
              {summary ? (
                <>
                  <p className="text-xs font-medium uppercase tracking-wider text-muted">Summary</p>
                  <div className="mt-3 flex items-baseline justify-between gap-4">
                    <span className="font-display text-base font-semibold">{summary.title}</span>
                    {summary.amount && (
                      <span className="font-display text-lg font-semibold">{summary.amount}</span>
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

              {intent?.kind === "subscription" && (
                <p className="mt-5 border-t border-outline pt-4 text-xs text-muted">
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
