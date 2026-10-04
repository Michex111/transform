/**
 * The rules behind the in-app "Payment method" section.
 *
 * Mounting a Payment Element with no client secret renders a blank iframe, and
 * mounting one when this build has no publishable key throws inside Stripe.js —
 * so these decisions are load-bearing and are pinned here rather than through a
 * component this repo's Node test environment cannot render.
 */

import { describe, expect, it } from "vitest";
import {
  CARD_EXPIRY_WARNING_DAYS,
  NO_PAYMENT_METHOD_SESSION_MESSAGE,
  NO_PUBLISHABLE_KEY_MESSAGE,
  SAVE_PAYMENT_METHOD_FAILED_MESSAGE,
  SETUP_NOT_COMPLETED_MESSAGE,
  STRIPE_NOT_READY_MESSAGE,
  addCardPrompt,
  billingDefaults,
  cardBrandLabel,
  cardExpiry,
  cardTitle,
  confirmPaymentMethodSetup,
  defaultCard,
  looksLikeClientSecret,
  needsPortalFallback,
  paymentElementElementOptions,
  paymentElementMount,
  paymentElementOptions,
  paymentMethodPanel,
  paymentMethodSectionVisible,
  walletLabel,
  type ConfirmPaymentMethodSetupInput,
} from "./paymentMethod";

const base = {
  hasSubscription: true,
  hasPublishableKey: true,
  session: null,
  failure: null,
};

describe("paymentMethodPanel", () => {
  it("hides the section for an account with no subscription", () => {
    // A Free account has no Stripe customer: there is no card to manage, and a
    // line about one would be noise. Checked before anything else, so it also
    // hides the no-key explanation.
    expect(paymentMethodPanel({ ...base, hasSubscription: false })).toEqual({ kind: "hidden" });
    expect(
      paymentMethodPanel({ ...base, hasSubscription: false, hasPublishableKey: false }),
    ).toEqual({ kind: "hidden" });
  });

  it("explains, rather than mounting, when this build has no publishable key", () => {
    const panel = paymentMethodPanel({ ...base, hasPublishableKey: false });
    expect(panel).toEqual({ kind: "explain", message: NO_PUBLISHABLE_KEY_MESSAGE });
  });

  it("is loading until the session request answers", () => {
    expect(paymentMethodPanel(base)).toEqual({ kind: "loading" });
  });

  it("mounts with the secret when the session is enabled", () => {
    const panel = paymentMethodPanel({
      ...base,
      session: { enabled: true, client_secret: "cs_test_abc" },
    });
    expect(panel).toEqual({ kind: "mount", clientSecret: "cs_test_abc" });
  });

  it("treats enabled=false as ordinary, not an error", () => {
    expect(
      paymentMethodPanel({ ...base, session: { enabled: false, client_secret: null } }),
    ).toEqual({ kind: "explain", message: NO_PAYMENT_METHOD_SESSION_MESSAGE });
    // An allowed session with no usable secret must not try to mount.
    expect(
      paymentMethodPanel({ ...base, session: { enabled: true, client_secret: null } }),
    ).toEqual({ kind: "explain", message: NO_PAYMENT_METHOD_SESSION_MESSAGE });
    expect(
      paymentMethodPanel({ ...base, session: { enabled: true, client_secret: "   " } }),
    ).toEqual({ kind: "explain", message: NO_PAYMENT_METHOD_SESSION_MESSAGE });
  });

  it("trims a real secret before handing it to Stripe.js", () => {
    const panel = paymentMethodPanel({
      ...base,
      session: { enabled: true, client_secret: "  cs_test_abc  " },
    });
    expect(panel).toEqual({ kind: "mount", clientSecret: "cs_test_abc" });
  });

  it("surfaces a failed request as a line, keeping the server's wording", () => {
    expect(paymentMethodPanel({ ...base, failure: "Payment method session failed" })).toEqual({
      kind: "explain",
      message: "Payment method session failed",
    });
    // A blank failure falls back to the ordinary explanation rather than an
    // empty line.
    expect(paymentMethodPanel({ ...base, failure: "   " })).toEqual({ kind: "loading" });
  });
});

describe("paymentMethodSectionVisible", () => {
  it("is false only for the hidden panel", () => {
    expect(paymentMethodSectionVisible({ kind: "hidden" })).toBe(false);
    expect(paymentMethodSectionVisible({ kind: "loading" })).toBe(true);
    expect(paymentMethodSectionVisible({ kind: "mount", clientSecret: "x" })).toBe(true);
    expect(paymentMethodSectionVisible({ kind: "explain", message: "x" })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// confirmPaymentMethodSetup — the extracted submit path
// ---------------------------------------------------------------------------

/**
 * The Stripe/Elements pair, faked as plain objects.
 *
 * No DOM and no Stripe.js: this is exactly the shape the component passes in,
 * which is the point of extracting the logic — it can be driven from Node.
 */
type StripeArg = ConfirmPaymentMethodSetupInput["stripe"];
type ElementsArg = ConfirmPaymentMethodSetupInput["elements"];

/** A fake `stripe` whose `confirmSetup` answers (or throws) as told. */
function fakeStripe(answer: unknown, opts: { throws?: unknown } = {}): StripeArg {
  const confirmSetup = async () => {
    if ("throws" in opts) throw opts.throws;
    return answer;
  };
  return { confirmSetup } as unknown as StripeArg;
}

/** Stand-in for the mounted Payment Element; never dereferenced. */
const ELEMENTS = {} as ElementsArg;

const RETURN_URL = "https://transform-to.com/app/billing";

function run(stripe: StripeArg, elements: ElementsArg = ELEMENTS) {
  return confirmPaymentMethodSetup({ stripe, elements, returnUrl: RETURN_URL });
}

describe("confirmPaymentMethodSetup", () => {
  it("reports success for a succeeded SetupIntent", async () => {
    await expect(run(fakeStripe({ setupIntent: { status: "succeeded" } }))).resolves.toEqual({
      ok: true,
    });
  });

  it("surfaces the message Stripe returns in its error object", async () => {
    await expect(
      run(fakeStripe({ error: { message: "Your card was declined." } })),
    ).resolves.toEqual({ ok: false, message: "Your card was declined." });
  });

  it("falls back to its own wording when a Stripe error carries no message", async () => {
    await expect(run(fakeStripe({ error: { message: "" } }))).resolves.toEqual({
      ok: false,
      message: SAVE_PAYMENT_METHOD_FAILED_MESSAGE,
    });
  });

  it("reports a thrown exception with its message", async () => {
    await expect(
      run(fakeStripe(undefined, { throws: new Error("Network error") })),
    ).resolves.toEqual({ ok: false, message: "Network error" });
  });

  it("falls back to its own wording when the thrown value is not an Error", async () => {
    await expect(run(fakeStripe(undefined, { throws: "boom" }))).resolves.toEqual({
      ok: false,
      message: SAVE_PAYMENT_METHOD_FAILED_MESSAGE,
    });
  });

  it("fails, rather than no-op success, when the form never mounted", async () => {
    await expect(run(null)).resolves.toEqual({
      ok: false,
      message: STRIPE_NOT_READY_MESSAGE,
    });
    await expect(run(null, null)).resolves.toEqual({
      ok: false,
      message: STRIPE_NOT_READY_MESSAGE,
    });
  });

  it("rejects a SetupIntent that is not succeeded", async () => {
    // e.g. the card needs 3DS approval that this flow never completed.
    await expect(
      run(fakeStripe({ setupIntent: { status: "requires_action" } })),
    ).resolves.toEqual({ ok: false, message: SETUP_NOT_COMPLETED_MESSAGE });
    // A response with neither an intent nor an error is malformed, not success.
    await expect(run(fakeStripe({}))).resolves.toEqual({
      ok: false,
      message: SETUP_NOT_COMPLETED_MESSAGE,
    });
  });
});

// ---------------------------------------------------------------------------
// Card presentation
// ---------------------------------------------------------------------------

describe("cardBrandLabel", () => {
  it("uses the name a cardholder would recognise", () => {
    expect(cardBrandLabel("visa")).toBe("Visa");
    expect(cardBrandLabel("mastercard")).toBe("Mastercard");
    expect(cardBrandLabel("amex")).toBe("American Express");
    expect(cardBrandLabel("diners")).toBe("Diners Club");
    expect(cardBrandLabel("jcb")).toBe("JCB");
  });

  it("is case- and whitespace-insensitive", () => {
    expect(cardBrandLabel("  VISA  ")).toBe("Visa");
  });

  it("degrades to a readable label for a brand it does not know", () => {
    // Stripe adds brands over time; printing the raw code would look broken.
    expect(cardBrandLabel("cartes_bancaires")).toBe("Cartes Bancaires");
    expect(cardBrandLabel("some_new_brand")).toBe("Some New Brand");
  });

  it("never claims a brand it does not have", () => {
    expect(cardBrandLabel("")).toBe("Card");
    expect(cardBrandLabel(null)).toBe("Card");
    expect(cardBrandLabel(undefined)).toBe("Card");
    expect(cardBrandLabel("unknown")).toBe("Card");
  });
});

describe("walletLabel", () => {
  it("names the wallet a card came from", () => {
    expect(walletLabel("apple_pay")).toBe("Apple Pay");
    expect(walletLabel("google_pay")).toBe("Google Pay");
    expect(walletLabel("link")).toBe("Link");
  });

  it("is null when there is no wallet, so no badge is rendered", () => {
    expect(walletLabel(null)).toBeNull();
    expect(walletLabel(undefined)).toBeNull();
    expect(walletLabel("")).toBeNull();
    expect(walletLabel("   ")).toBeNull();
  });
});

describe("cardTitle", () => {
  it("joins the brand and the masked number", () => {
    expect(cardTitle({ brand: "visa", last4: "4242" })).toBe("Visa •••• 4242");
  });

  it("omits the mask when there is no last four to show", () => {
    expect(cardTitle({ brand: "visa", last4: "" })).toBe("Visa");
  });
});

describe("cardExpiry", () => {
  it("treats the card as valid through the END of its expiry month", () => {
    // A card marked 01/2027 is still good in January 2027 — the first version
    // of this flagged it expired for up to 31 days.
    const result = cardExpiry(
      { exp_month: 1, exp_year: 2027 },
      new Date("2027-01-15T00:00:00Z"),
    );
    expect(result.state).toBe("expiring");
    expect(result.label).toBe("01/2027");
  });

  it("is expired once the following month starts", () => {
    const result = cardExpiry(
      { exp_month: 1, exp_year: 2027 },
      new Date("2027-02-01T00:00:00Z"),
    );
    expect(result.state).toBe("expired");
    expect(result.daysLeft).toBeLessThanOrEqual(0);
  });

  it("warns inside the warning window and is quiet outside it", () => {
    // 06/2027 lapses at 2027-07-01T00:00Z, so the warning window opens exactly
    // 60 days earlier. Pin the boundary from both sides rather than picking two
    // arbitrary dates: an off-by-one here is a card that either warns too late
    // to be acted on, or nags for two months about nothing.
    const card = { exp_month: 6, exp_year: 2027 };

    const onTheBoundary = cardExpiry(card, new Date("2027-05-02T00:00:00Z"));
    expect(onTheBoundary.state).toBe("expiring");
    expect(onTheBoundary.daysLeft).toBe(CARD_EXPIRY_WARNING_DAYS);

    const oneDayEarlier = cardExpiry(card, new Date("2027-05-01T00:00:00Z"));
    expect(oneDayEarlier.state).toBe("ok");
    expect(oneDayEarlier.daysLeft).toBe(CARD_EXPIRY_WARNING_DAYS + 1);

    expect(cardExpiry(card, new Date("2027-01-01T00:00:00Z")).state).toBe("ok");
  });

  it("pads a single-digit month the way a card prints it", () => {
    expect(cardExpiry({ exp_month: 3, exp_year: 2030 }).label).toBe("03/2030");
  });

  it("stays silent rather than inventing an expiry it was not given", () => {
    // A malformed payload normalises to 0/0. "Expires 00/0000" — or worse, a
    // claim that the card has expired — would both be false.
    for (const bad of [
      { exp_month: 0, exp_year: 0 },
      { exp_month: 13, exp_year: 2030 },
      { exp_month: 5, exp_year: 0 },
      { exp_month: 5, exp_year: 1900 },
    ]) {
      const result = cardExpiry(bad);
      expect(result.state).toBe("ok");
      expect(result.label).toBe("");
      expect(result.daysLeft).toBeNull();
    }
  });
});

describe("defaultCard", () => {
  it("finds the flagged card", () => {
    const card = { id: "pm_2", is_default: true };
    expect(defaultCard([{ id: "pm_1", is_default: false }, card])).toBe(card);
  });

  it("is null when Stripe did not say which card is charged", () => {
    expect(defaultCard([{ id: "pm_1", is_default: false }])).toBeNull();
    expect(defaultCard([])).toBeNull();
  });
});

describe("addCardPrompt", () => {
  it("explains why a plan with no card cannot renew", () => {
    expect(addCardPrompt([])).toMatch(/renew/i);
  });

  it("suggests a backup when a single decline would interrupt the plan", () => {
    expect(addCardPrompt([{}])).toMatch(/backup/i);
  });

  it("says nothing once there is redundancy", () => {
    expect(addCardPrompt([{}, {}])).toBeNull();
    expect(addCardPrompt([{}, {}, {}])).toBeNull();
  });
});

describe("billingDefaults", () => {
  it("prefers the account's real name over its display name", () => {
    expect(
      billingDefaults({
        first_name: "Ada",
        last_name: "Lovelace",
        display_name: "ada",
        email: "ada@example.com",
      }),
    ).toEqual({ name: "Ada Lovelace", email: "ada@example.com" });
  });

  it("falls back to the display name when no real name is set", () => {
    expect(billingDefaults({ display_name: "Ada Lovelace" })).toEqual({ name: "Ada Lovelace" });
  });

  it("omits a field rather than prefilling it with an empty string", () => {
    // Passing "" suppresses Stripe's own placeholder, so absence matters.
    expect(billingDefaults(null)).toEqual({});
    expect(billingDefaults({})).toEqual({});
    expect(billingDefaults({ first_name: "  ", email: "   " })).toEqual({});
    expect(billingDefaults({ first_name: "Ada", last_name: null })).toEqual({ name: "Ada" });
  });
});

// ---------------------------------------------------------------------------
// Mounting the Payment Element
// ---------------------------------------------------------------------------

describe("paymentElementMount", () => {
  it("uses the server's SetupIntent when there is one", () => {
    expect(paymentElementMount("seti_1ABC_secret_xyz")).toEqual({
      kind: "setup-intent",
      clientSecret: "seti_1ABC_secret_xyz",
    });
  });

  it("trims before deciding", () => {
    expect(paymentElementMount("  seti_1ABC_secret_xyz  ")).toEqual({
      kind: "setup-intent",
      clientSecret: "seti_1ABC_secret_xyz",
    });
  });

  it("falls back to the deferred mode for an older API or a blank secret", () => {
    expect(paymentElementMount(null)).toEqual({ kind: "deferred" });
    expect(paymentElementMount(undefined)).toEqual({ kind: "deferred" });
    expect(paymentElementMount("   ")).toEqual({ kind: "deferred" });
  });

  it("falls back rather than handing Stripe a malformed secret", () => {
    // Stripe THROWS on these, inside the render of <Elements>, which unmounts
    // whatever tree the Element sits in. Verified in a browser: the whole
    // Billing page went blank. Falling back to the deferred mode costs one
    // round-trip and cannot blank the page.
    expect(paymentElementMount("seti_secret_1")).toEqual({ kind: "deferred" });
    expect(paymentElementMount("secret_abc")).toEqual({ kind: "deferred" });
    expect(paymentElementMount("abcsecret")).toEqual({ kind: "deferred" });
  });
});

describe("looksLikeClientSecret", () => {
  it("accepts a Stripe-shaped secret", () => {
    expect(looksLikeClientSecret("seti_1ABC_secret_xyz")).toBe(true);
    expect(looksLikeClientSecret("cs_test_a1b2c3_secret_xyz")).toBe(true);
  });

  it("rejects anything it cannot confirm is a secret", () => {
    for (const value of [
      "",
      "   ",
      null,
      undefined,
      // A truncated or hand-written value: the id part has no underscore, so
      // it is not `<id>_secret_<random>`.
      "seti_secret_1",
      "secret_abc",
      "abcsecret",
      "abc_secret_def_ghi",
    ]) {
      expect(looksLikeClientSecret(value)).toBe(false);
    }
  });

  it("does not reject the opaque Customer Session secret shape", () => {
    // Documented as deliberately NOT checked by this rule — the Customer
    // Session secret is opaque (Stripe's own example is `_POpx…Jgje`), so a
    // shape check would wrongly refuse a real one and the form could never
    // mount. Only the intent secret has a checkable form.
    expect(looksLikeClientSecret("_POpxYpmkXdtttYtZQYhrsOJZ2RCQ9kCqqXRU6qrP5c4Jgje")).toBe(false);
  });
});

describe("paymentElementOptions", () => {
  it("passes clientSecret and the customer session together for a SetupIntent", () => {
    const options = paymentElementOptions("cs_seti_1", "seti_1ABC_secret_xyz");
    expect(options).toMatchObject({ clientSecret: "seti_1ABC_secret_xyz" });
    expect(options).toMatchObject({ customerSessionClientSecret: "cs_seti_1" });
  });

  it("never passes clientSecret and mode together — Stripe rejects that pair", () => {
    const withIntent = paymentElementOptions("cs_seti_1", "seti_1ABC_secret_xyz");
    expect("mode" in withIntent).toBe(false);

    const deferred = paymentElementOptions("cs_seti_1", null);
    expect("clientSecret" in deferred).toBe(false);
    expect(deferred).toMatchObject({ mode: "setup", currency: "usd" });
    expect(deferred).toMatchObject({ customerSessionClientSecret: "cs_seti_1" });
  });

  it("always carries the brand appearance", () => {
    expect(paymentElementOptions("cs_seti_1", null)).toHaveProperty("appearance");
    expect(paymentElementOptions("cs_seti_1", "seti_1ABC_secret_xyz")).toHaveProperty("appearance");
  });
});

describe("paymentElementElementOptions", () => {
  it("prefills only the details it actually has", () => {
    expect(paymentElementElementOptions({ name: "Ada", email: "ada@example.com" })).toMatchObject({
      defaultValues: { billingDetails: { name: "Ada", email: "ada@example.com" } },
    });
  });

  it("omits defaultValues entirely when there is nothing to prefill", () => {
    const options = paymentElementElementOptions({});
    expect("defaultValues" in options).toBe(false);
  });

  it("keeps the wallets available", () => {
    expect(paymentElementElementOptions({})).toMatchObject({
      wallets: { applePay: "auto", googlePay: "auto" },
    });
  });
});

describe("needsPortalFallback", () => {
  it("is true only when this build cannot mount the card form", () => {
    // Derived through `paymentMethodPanel` rather than built by hand: the two
    // functions share the message, so if the no-key branch ever returns
    // different copy this stops matching and the test fails. Building the
    // object literally would have made the check tautological.
    const panel = paymentMethodPanel({ ...base, hasPublishableKey: false });
    expect(needsPortalFallback(panel)).toBe(true);
  });

  it("is false for states where the portal would be a dead end too", () => {
    // No customer yet, or a failed request — the account genuinely has nothing
    // to manage, so sending them to Stripe would just be another dead end.
    const noSession = paymentMethodPanel({
      ...base,
      session: { enabled: false, client_secret: null },
    });
    const failed = paymentMethodPanel({ ...base, failure: "Payment method session failed" });

    expect(needsPortalFallback(noSession)).toBe(false);
    expect(needsPortalFallback(failed)).toBe(false);
    expect(needsPortalFallback({ kind: "hidden" })).toBe(false);
    expect(needsPortalFallback({ kind: "loading" })).toBe(false);
    expect(needsPortalFallback({ kind: "mount", clientSecret: "cs_1" })).toBe(false);
  });
});
