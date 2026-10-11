/**
 * The checkout page must never confirm a session other than the one it shows.
 *
 * This is a source-reading test, in the same style as
 * `lib/stripeAppearance.test.ts` (reads `index.css`) and
 * `vite-plugins/spa-route-stubs.test.ts` (reads `App.tsx`). It has to be: the
 * fault it guards against is invisible to every other kind of test available
 * here.
 *
 *   - Stripe's `CheckoutElementsProvider` initialises its SDK exactly once and
 *     IGNORES a later `clientSecret` (an internal ref guards
 *     `initCheckoutElementsSdk` and is never reset), so the page can only make
 *     it pick up a new session by changing the React `key`.
 *   - This repo's vitest environment is Node with no DOM, so a remount cannot
 *     be observed by rendering — only by the `key` being present in the JSX.
 *
 * Without the key, applying a promotion code updates the order summary to
 * "Free — first month" while `confirm()` still targets the previous, full-price
 * session. That shipped once: a customer's card was charged $9.99 for a product
 * the page had told them was free.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SOURCE = fileURLToPath(new URL("./CheckoutPage.tsx", import.meta.url));

function pageSource(): string {
  return readFileSync(SOURCE, "utf8");
}

/**
 * The `<CheckoutElementsProvider …>` opening tag.
 *
 * The element is located with `\s` after the tag name, because the page also
 * mentions `<CheckoutElementsProvider>` in prose (a doc comment), and that
 * occurrence would otherwise be matched first and yield an empty tag.
 *
 * Sliced to the first `>` after the tag name rather than matched with a regex:
 * the tag's `options` prop contains `>`-free object literals, so the first `>`
 * reliably ends it, while a lazy `[\s\S]*?` could silently span a whole block
 * and keep passing after the key was moved somewhere meaningless.
 */
function providerOpeningTag(text: string): string {
  const start = text.search(/<CheckoutElementsProvider\s/);
  if (start === -1) {
    throw new Error("CheckoutPage.tsx no longer renders <CheckoutElementsProvider>");
  }
  return text.slice(start, text.indexOf(">", start));
}

describe("CheckoutPage session binding", () => {
  it("keys the checkout provider by the session secret", () => {
    // The key is what forces the remount the provider will not do by itself.
    // Derived from `clientSecret`, so a new session yields a new key.
    expect(providerOpeningTag(pageSource())).toContain("key={checkoutElementsKey(clientSecret)}");
  });

  it("labels the button from the mounted session's own total", () => {
    const text = pageSource();

    // `total.total.minorUnitsAmount` is read off the session the form is
    // actually bound to; the API's echoed `amount_total` is a different object
    // and is exactly what disagreed when the bug above shipped.
    expect(text).toContain("checkoutButtonLabel(mountedTotal)");
    expect(text).toContain("checkoutCardHint(mountedTotal)");
    expect(text).toContain("checkout.total.total.minorUnitsAmount");

    // Guards the regression directly: wording must never come from the echoed
    // total again.
    expect(text).not.toContain("checkoutButtonLabel(amountTotal)");
    expect(text).not.toContain("checkoutCardHint(amountTotal)");
  });

  it("hides the form while a new session is being created", () => {
    // The stale form is bound to the previous session. Leaving it on screen
    // during the swap would reopen the same window even with the key in place,
    // so the effect must drop back to the skeleton BEFORE it creates the next
    // session — hence `setPhase("creating")` ahead of the awaited call.
    expect(pageSource()).toMatch(/setPhase\("creating"\);[\s\S]{0,300}?void \(async \(\) => \{/);
  });
});
