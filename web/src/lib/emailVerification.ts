/**
 * Email-verification page logic, kept out of React so it can be tested.
 *
 * This module exists because of a specific bug. The verification effect used
 * the familiar "cancel on unmount" closure flag:
 *
 *     useEffect(() => {
 *       if (attempted.current) return
 *       attempted.current = true
 *       let active = true
 *       api.verifyEmail(token).then((r) => { if (!active) return; setStatus(...) })
 *       return () => { active = false }
 *     }, [token])
 *
 * Under React 18 StrictMode that combination is broken. The effects run twice:
 * the first run starts the request, the cleanup sets `active = false`, and the
 * second run returns early on `attempted` — so `active` is never restored to
 * `true` for the closure that owns the pending promise. The API answered 200
 * and the page sat on "Verifying your email…" forever. The single-flight guard
 * and the unmount guard were fighting each other.
 *
 * Splitting them fixes it: single-flight lives here (outside React, so a remount
 * cannot duplicate the request or orphan its result), and the component keeps a
 * plain per-run `cancelled` flag that StrictMode re-initialises correctly. The
 * repo has no DOM test library, so this logic is also the only part that can be
 * regression-tested directly.
 */

import type { VerifyEmailResponse } from "@/api/types";

export type VerificationStatus = "verifying" | "verified" | "already" | "invalid" | "missing";

/** A verifier: consumes a token and reports the outcome. */
export type TokenVerifier = (token: string) => Promise<VerifyEmailResponse>;

/**
 * Wrap a verifier so each token is submitted **at most once per page load**.
 *
 * Verification tokens are single-use: a second submission of the same token
 * matches no row and is reported as "invalid or expired". So a duplicate request
 * does not merely waste a round-trip — it turns a successful activation into a
 * visible failure for the user. React 18 StrictMode double-invokes effects in
 * development, which makes that duplicate the default outcome unless it is
 * guarded.
 *
 * The cache is keyed by token and never evicted. That is deliberate: it is
 * scoped to this module (one page load), the map holds at most a handful of
 * entries, and a spent token must not become retryable by a remount. A failed
 * attempt is cached too — the token is unusable either way, and retrying a
 * rejected credential is not useful behaviour.
 */
export function createSingleFlightVerifier(verify: TokenVerifier): TokenVerifier {
  const attempts = new Map<string, Promise<VerifyEmailResponse>>();

  return (token: string) => {
    const existing = attempts.get(token);
    if (existing) return existing;

    const promise = verify(token);
    attempts.set(token, promise);
    return promise;
  };
}

/**
 * The status to render for a successful response.
 *
 * `already_verified` becomes its own status rather than being folded into
 * `verified` because the two deserve different copy: "we just activated you"
 * versus "you were activated at some point — you may have clicked this link
 * twice or opened it on a second device". Reporting the latter as a fresh
 * success is confusing; reporting it as an error is alarming and wrong.
 */
export function verificationStatusFor(result: VerifyEmailResponse): VerificationStatus {
  return result.already_verified ? "already" : "verified";
}
