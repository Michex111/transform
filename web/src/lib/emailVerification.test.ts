/**
 * Regression tests for the email-verification page logic.
 *
 * Context for the first block: the page originally combined a "run once" ref
 * with a "cancel on unmount" closure flag. Under React 18 StrictMode those
 * cancel each other out — run 1 starts the request, the cleanup sets
 * `active = false`, and run 2 returns early on the ref so `active` is never
 * restored. The API answered 200 and the page stayed on "Verifying your
 * email…" forever (confirmed in a real browser, not theorised).
 *
 * The fix moved single-flight out of React, which is what these tests pin: the
 * request is issued exactly once, and the *same* promise is handed to every
 * caller, so a second effect run still receives a result to render.
 */

import { describe, expect, it, vi } from "vitest";
import { createSingleFlightVerifier, verificationStatusFor } from "@/lib/emailVerification";
import type { VerifyEmailResponse } from "@/api/types";

function response(overrides: Partial<VerifyEmailResponse> = {}): VerifyEmailResponse {
  return {
    ok: true,
    already_verified: false,
    username: "ada",
    message: "Verified.",
    ...overrides,
  };
}

describe("createSingleFlightVerifier", () => {
  it("issues exactly one request per token", async () => {
    // The StrictMode scenario: two calls for the same single-use token.
    const verify = vi.fn().mockResolvedValue(response());
    const verifyOnce = createSingleFlightVerifier(verify);

    await Promise.all([verifyOnce("tok-1"), verifyOnce("tok-1")]);

    expect(verify).toHaveBeenCalledTimes(1);
  });

  it("hands every caller the same promise, so a second effect run still renders", async () => {
    // This is the property the old code lacked: the second run must be able to
    // observe the outcome, not have it discarded.
    const verify = vi.fn().mockResolvedValue(response({ username: "grace" }));
    const verifyOnce = createSingleFlightVerifier(verify);

    const first = verifyOnce("tok-1");
    const second = verifyOnce("tok-1");

    expect(second).toBe(first);
    await expect(second).resolves.toMatchObject({ username: "grace" });
  });

  it("does not cache ACROSS tokens", async () => {
    // Each link carries its own token; memoising globally would make a second
    // user's link return the first user's result.
    const verify = vi.fn().mockImplementation((token: string) =>
      Promise.resolve(response({ username: token })),
    );
    const verifyOnce = createSingleFlightVerifier(verify);

    await expect(verifyOnce("tok-a")).resolves.toMatchObject({ username: "tok-a" });
    await expect(verifyOnce("tok-b")).resolves.toMatchObject({ username: "tok-b" });

    expect(verify).toHaveBeenCalledTimes(2);
  });

  it("caches a rejection too, so a bad token is not retried into a different verdict", async () => {
    // The token is unusable either way, and a retry could only ever produce a
    // second, more confusing message.
    const verify = vi.fn().mockRejectedValue(new Error("invalid or expired"));
    const verifyOnce = createSingleFlightVerifier(verify);

    await expect(verifyOnce("tok-1")).rejects.toThrow("invalid or expired");
    await expect(verifyOnce("tok-1")).rejects.toThrow("invalid or expired");

    expect(verify).toHaveBeenCalledTimes(1);
  });

  it("surfaces an in-flight request synchronously as a promise", () => {
    // The page calls `.then` immediately; a verifier that could return
    // undefined for a duplicate call would throw a TypeError instead.
    const verifyOnce = createSingleFlightVerifier(() => new Promise(() => {}));

    expect(verifyOnce("tok-1")).toBeInstanceOf(Promise);
    expect(verifyOnce("tok-1")).toBeInstanceOf(Promise);
  });
});

describe("verificationStatusFor", () => {
  it("reports a fresh activation as 'verified'", () => {
    expect(verificationStatusFor(response())).toBe("verified");
  });

  it("reports a repeat click as 'already'", () => {
    // Distinct copy: "you were already verified" reads very differently from
    // "we just activated you", and neither should be shown as an error.
    expect(verificationStatusFor(response({ already_verified: true }))).toBe("already");
  });

  it("treats a missing already_verified flag as a fresh activation", () => {
    // Normalised responses always carry the field; if one ever does not, the
    // safe reading is the success the API also reports via `ok`.
    const partial = { ok: true, message: "" } as VerifyEmailResponse;
    expect(verificationStatusFor(partial)).toBe("verified");
  });
});
