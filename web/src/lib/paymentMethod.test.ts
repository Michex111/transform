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
  paymentMethodPanel,
  paymentMethodSectionVisible,
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
