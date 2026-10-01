/**
 * The Payment Element's brand overlay must not drift from the app it sits in.
 *
 * `stripeAppearance.ts` duplicates the values that live in `index.css`'s
 * `@theme` block as literal hex, because Stripe's Appearance API cannot read
 * our CSS custom properties from inside its iframe. That is a cross-language
 * copy with no compiler behind it: recolouring the app would silently leave the
 * card form on the old palette.
 *
 * Reading the stylesheet as text turns that silent drift into a failing test.
 * The same technique is used by `vite-plugins/spa-route-stubs.test.ts` (reads
 * `App.tsx`) and by the backend's `test_stripe_embedded_checkout.py` (reads this
 * very file). Node's `fs` is available because the vitest environment is `node`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { STRIPE_CHECKOUT_APPEARANCE } from "./stripeAppearance";

const INDEX_CSS = fileURLToPath(new URL("../index.css", import.meta.url));

/**
 * Every `--token: value;` declaration inside the `@theme` block.
 *
 * The block is matched non-greedily up to the first column-0 closing brace; it
 * has no nested braces (only declarations), so that is the whole block.
 */
function designTokens(): Record<string, string> {
  const css = readFileSync(INDEX_CSS, "utf8");
  const block = css.match(/@theme\s*\{([\s\S]*?)\n\}/);
  if (!block) throw new Error(`No @theme block found in ${INDEX_CSS}`);

  const tokens: Record<string, string> = {};
  for (const match of block[1].matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

const tokens = designTokens();

describe("STRIPE_CHECKOUT_APPEARANCE", () => {
  it("uses Stripe's built-in night theme as the base", () => {
    // The built-in theme supplies the dozens of variables not overridden below
    // (icons, placeholders, focus rings) in a dark palette. Losing it would mean
    // maintaining all of them here.
    expect(STRIPE_CHECKOUT_APPEARANCE.theme).toBe("night");
  });

  it("takes its colours from the @theme tokens", () => {
    const variables = STRIPE_CHECKOUT_APPEARANCE.variables;
    if (!variables) throw new Error("appearance has no variables");

    expect(variables.colorPrimary).toBe(tokens["color-primary"]);
    expect(variables.colorBackground).toBe(tokens["color-background"]);
    expect(variables.colorText).toBe(tokens["color-on-background"]);
    expect(variables.colorDanger).toBe(tokens["color-error"]);
  });

  it("takes its font family from the body font token", () => {
    const variables = STRIPE_CHECKOUT_APPEARANCE.variables;
    if (!variables) throw new Error("appearance has no variables");

    // Compared verbatim, fallback stack included: a change to `--font-body`
    // (say, dropping Inter) must fail here rather than leave the card fields on
    // the previous typeface.
    expect(variables.fontFamily).toBe(tokens["font-body"]);
  });

  it("invents no colour that is not already a design token", () => {
    // The general guard. The checks above name the variables we know about; this
    // one catches a *new* literal added to `variables` or `rules` later, so a
    // hand-picked hex cannot slip in unnoticed.
    const literals = JSON.stringify(STRIPE_CHECKOUT_APPEARANCE).match(/#[0-9a-fA-F]{3,8}/g) ?? [];
    expect(literals.length).toBeGreaterThan(0);

    const known = new Set(Object.values(tokens));
    for (const literal of literals) {
      expect(known).toContain(literal);
    }
  });
});
