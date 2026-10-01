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
  NO_PAYMENT_METHOD_SESSION_MESSAGE,
  NO_PUBLISHABLE_KEY_MESSAGE,
  SAVE_PAYMENT_METHOD_FAILED_MESSAGE,
  SETUP_NOT_COMPLETED_MESSAGE,
  STRIPE_NOT_READY_MESSAGE,
  confirmPaymentMethodSetup,
  paymentMethodPanel,
  paymentMethodSectionVisible,
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
