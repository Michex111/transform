/**
 * The decision logic behind the branded checkout.
 *
 * These rules decide whether the app asks for an embedded session at all, and
 * what it thinks it is selling. Both are load-bearing in a way that is easy to
 * get wrong and expensive to get wrong: asking for `embedded` without a
 * publishable key to mount Stripe.js produces a checkout button that does
 * nothing, and a mis-parsed query string creates a session for the wrong thing.
 *
 * `parseCheckoutIntent` takes the raw search string rather than reading
 * `location`, so it is testable here (this repo's vitest environment is Node,
 * with no `window`).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
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
} from "./stripeCheckout";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("parseCheckoutIntent", () => {
  it("reads a subscription tier", () => {
    expect(parseCheckoutIntent("?tier=PRO")).toEqual({ kind: "subscription", tier: "PRO" });
  });

  it("normalises the tier's casing", () => {
    // The API's enum is uppercase; a hand-typed or lowercased link must not
    // become a 422 the user cannot act on.
    expect(parseCheckoutIntent("?tier=pro_plus")).toEqual({
      kind: "subscription",
      tier: "PRO_PLUS",
    });
  });

  it("reads a credit pack", () => {
    expect(parseCheckoutIntent("?credits=100")).toEqual({ kind: "credits", amount: 100 });
  });

  it("prefers the tier when a link somehow carries both", () => {
    expect(parseCheckoutIntent("?credits=100&tier=PRO")).toEqual({
      kind: "subscription",
      tier: "PRO",
    });
  });

  it("leaves the tier's validity to the API", () => {
    // Not validated against the plan list on purpose: a plan rename must not
    // silently turn a valid upgrade into a dead end, and the API already
    // answers a bad tier with a message the user can act on.
    expect(parseCheckoutIntent("?tier=FREE")).toEqual({ kind: "subscription", tier: "FREE" });
    expect(parseCheckoutIntent("?tier=NOT_A_TIER")).toEqual({
      kind: "subscription",
      tier: "NOT_A_TIER",
    });
  });

  it.each([
    ["", "an empty search string"],
    ["?tier=", "an empty tier"],
    ["?tier=   ", "a whitespace tier"],
    ["?credits=", "an empty credit amount"],
    ["?credits=0", "zero credits"],
    ["?credits=-5", "negative credits"],
    ["?credits=10.5", "a fractional credit amount"],
    ["?credits=lots", "a non-numeric credit amount"],
    ["?other=1", "an unrelated parameter"],
  ])("returns null for %s (%s)", (search) => {
    expect(parseCheckoutIntent(search)).toBeNull();
  });

  it("rejects a tier that could not be an enum value", () => {
    // The value is echoed into a URL and returned to the API; keeping it to the
    // enum's alphabet means nothing else can ride along.
    expect(parseCheckoutIntent("?tier=pro%20plus")).toBeNull();
    expect(parseCheckoutIntent("?tier=PRO/../etc")).toBeNull();
    expect(parseCheckoutIntent("?tier=1PRO")).toBeNull();
  });

  it("tolerates surrounding whitespace in a credit amount", () => {
    expect(parseCheckoutIntent("?credits=%20100%20")).toEqual({ kind: "credits", amount: 100 });
  });
});

describe("describeIntent", () => {
  it("names a plan without its underscore", () => {
    expect(describeIntent({ kind: "subscription", tier: "PRO_PLUS" })).toBe(
      "the PRO PLUS plan",
    );
  });

  it("names a credit pack", () => {
    expect(describeIntent({ kind: "credits", amount: 500 })).toBe("500 conversion credits");
  });
});

describe("embedded checkout availability", () => {
  it("is off when no publishable key is configured", () => {
    vi.stubEnv("VITE_STRIPE_PUBLISHABLE_KEY", "");
    expect(embeddedCheckoutEnabled()).toBe(false);
    // The important half: a build with no key must ask the API for the page it
    // has always used, so merging this feature cannot change how anyone pays.
    expect(requestedUiMode()).toBe("hosted");
  });

  it("is off for a value that is not a publishable key", () => {
    // e.g. a secret key pasted into the wrong variable. Handing that to
    // `loadStripe` throws inside Stripe.js, leaving a blank checkout.
    vi.stubEnv("VITE_STRIPE_PUBLISHABLE_KEY", "sk_test_oops");
    expect(embeddedCheckoutEnabled()).toBe(false);
    expect(requestedUiMode()).toBe("hosted");
  });

  it("is on for a real publishable key", () => {
    vi.stubEnv("VITE_STRIPE_PUBLISHABLE_KEY", "pk_test_abc123");
    expect(embeddedCheckoutEnabled()).toBe(true);
    expect(requestedUiMode()).toBe("elements");
    expect(publishableKey()).toBe("pk_test_abc123");
  });

  it("ignores padding around the key", () => {
    vi.stubEnv("VITE_STRIPE_PUBLISHABLE_KEY", "  pk_live_abc  ");
    expect(publishableKey()).toBe("pk_live_abc");
    expect(embeddedCheckoutEnabled()).toBe(true);
  });
});

describe("promotion codes in the checkout intent", () => {
  it("carries a code from the URL", () => {
    expect(parseCheckoutIntent("?tier=PRO&promo=CAMPUS2026")).toEqual({
      kind: "subscription",
      tier: "PRO",
      promo: "CAMPUS2026",
    });
  });

  it("leaves a URL without a code exactly as it was", () => {
    // `promo` is absent, not present-and-undefined, so existing callers and
    // tests that compare with `toEqual` are untouched.
    const intent = parseCheckoutIntent("?tier=PRO");
    expect(intent).toEqual({ kind: "subscription", tier: "PRO" });
    expect(intent).not.toHaveProperty("promo");
  });

  it("treats a blank or whitespace-only code as absent", () => {
    expect(parseCheckoutIntent("?tier=PRO&promo=")).toEqual({ kind: "subscription", tier: "PRO" });
    expect(parseCheckoutIntent("?tier=PRO&promo=%20%20")).toEqual({
      kind: "subscription",
      tier: "PRO",
    });
  });

  it("trims a padded code but keeps its casing", () => {
    // Stripe matches case-insensitively, and echoing the customer's own casing
    // back is friendlier than shouting it.
    expect(parseCheckoutIntent("?tier=PRO&promo=%20campus2026%20")).toEqual({
      kind: "subscription",
      tier: "PRO",
      promo: "campus2026",
    });
  });

  it("carries a code with a credit pack too", () => {
    expect(parseCheckoutIntent("?credits=100&promo=GIFT")).toEqual({
      kind: "credits",
      amount: 100,
      promo: "GIFT",
    });
  });

  it("normalises a raw code value", () => {
    expect(normalizePromoCode("  CAMPUS2026 ")).toBe("CAMPUS2026");
    expect(normalizePromoCode("")).toBeUndefined();
    expect(normalizePromoCode("   ")).toBeUndefined();
    expect(normalizePromoCode(null)).toBeUndefined();
    expect(normalizePromoCode(undefined)).toBeUndefined();
  });
});

describe("promo URL builders", () => {
  it("adds the code to the current URL", () => {
    expect(checkoutUrlWithPromo("?tier=PRO", "CAMPUS2026")).toBe(
      "/app/checkout?tier=PRO&promo=CAMPUS2026",
    );
  });

  it("replaces a code that is already there", () => {
    expect(checkoutUrlWithPromo("?tier=PRO&promo=OLD", "NEW")).toBe(
      "/app/checkout?tier=PRO&promo=NEW",
    );
  });

  it("encodes a code with spaces and reserved characters", () => {
    // Round-trips through the parser, which is what the page actually relies on.
    const url = checkoutUrlWithPromo("?tier=PRO", "Campus 2026 & Co");
    expect(url).toContain("promo=Campus%202026%20%26%20Co");
    const search = url.slice(url.indexOf("?"));
    expect(parseCheckoutIntent(search)).toEqual({
      kind: "subscription",
      tier: "PRO",
      promo: "Campus 2026 & Co",
    });
  });

  it("drops only the code when removing it", () => {
    // The escape hatch for a mistyped code must keep the purchase intact.
    expect(checkoutUrlWithoutPromo("?tier=PRO&promo=BAD")).toBe("/app/checkout?tier=PRO");
    expect(checkoutUrlWithoutPromo("?tier=PRO")).toBe("/app/checkout?tier=PRO");
    expect(checkoutUrlWithoutPromo("?promo=BAD")).toBe("/app/checkout");
  });
});

describe("checkoutButtonLabel", () => {
  it("invites a free start when nothing is due", () => {
    expect(checkoutButtonLabel(0)).toBe("Start my free month");
  });

  it("keeps the paying wording when there is an amount", () => {
    expect(checkoutButtonLabel(999)).toBe("Pay");
  });

  it("defaults to paying when the API did not report a total", () => {
    // An older API omits `amount_total`. Claiming something is free on missing
    // data would be the worst possible failure mode here.
    expect(checkoutButtonLabel(null)).toBe("Pay");
    expect(checkoutButtonLabel(undefined)).toBe("Pay");
  });
});

describe("checkoutCardHint", () => {
  it("says no card is required for a zero total", () => {
    expect(checkoutCardHint(0)).toContain("No card required");
  });

  it("keeps the Stripe reassurance when a card is collected", () => {
    expect(checkoutCardHint(999)).toContain("Stripe");
    expect(checkoutCardHint(null)).toContain("Stripe");
  });
});
