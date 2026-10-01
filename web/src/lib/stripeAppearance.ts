/**
 * The brand overlay for Stripe's Payment Element.
 *
 * Stripe renders the card fields inside its own iframe, so the only way to make
 * them match the app is Stripe's Appearance API — and that API takes **literal
 * values**, not our `var(--token)` custom properties (there is no `:root` in the
 * iframe to resolve them against). The values therefore have to be written out
 * as hex here.
 *
 * Writing them out is unavoidable; letting them drift silently is not. This is
 * a cross-language copy with no compiler behind it, exactly like the backend's
 * `branding_settings` for embedded Checkout. `stripeAppearance.test.ts` reads
 * `web/src/index.css`'s `@theme` block and fails if these literals stop matching
 * the tokens, so recolouring the app can no longer leave the payment form on the
 * old palette unnoticed.
 *
 * `theme: "night"` is kept as the base because Stripe's built-in dark theme fills
 * in the many variables not overridden below (icons, placeholders, focus rings);
 * the overrides then pull the parts a user actually notices onto our palette.
 */

import type { Appearance } from "@stripe/stripe-js";

/**
 * Appearance for the in-page Payment Element.
 *
 * Every literal here is a copy of a token in `web/src/index.css`'s `@theme`
 * block; see the file header. Values that are *not* a token (the `borderRadius`
 * and the `rules` shape) are described where they are set.
 */
export const STRIPE_CHECKOUT_APPEARANCE: Appearance = {
  theme: "night",
  variables: {
    // Brand ramp — `--color-primary`, `--color-background`, `--color-on-background`,
    // `--color-error`. `fontFamily` is `--font-body` verbatim, including the
    // fallback stack, so the form's type matches the page's.
    colorPrimary: "#5a6bff",
    colorBackground: "#121417",
    colorText: "#eceef1",
    colorDanger: "#f43f5e",
    fontFamily: '"Inter", system-ui, sans-serif',
    // Matches the 8px radius the design system uses for inputs and cards
    // (`web/design-system/DESIGN.md`, "Layout").
    borderRadius: "8px",
  },
  rules: {
    // The inputs otherwise keep Stripe's own fill and hairline, which reads as a
    // foreign panel dropped onto our surfaces. These mirror the app's inputs:
    // `--color-surface` fill, `--color-outline` border, `--color-primary` focus.
    ".Input": {
      backgroundColor: "#1b1e23",
      border: "1px solid #2a2e34",
      boxShadow: "none",
    },
    ".Input:focus": {
      border: "1px solid #5a6bff",
      boxShadow: "0 0 0 1px #5a6bff",
    },
    // `--color-muted`, so a field label is quiet rather than full-strength text.
    ".Label": {
      color: "#8a919b",
    },
  },
};
