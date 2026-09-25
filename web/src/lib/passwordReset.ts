/**
 * Password-reset page logic, kept out of React so it can be tested.
 *
 * The repo's vitest environment is `node` (no jsdom, no testing-library), so
 * anything a page *decides* — is there a token, is the password acceptable,
 * which copy explains a failure — has to live in a pure module to be
 * regression-testable. Same workaround as `lib/emailVerification.ts`,
 * `lib/jobStore.ts` and `lib/formatRoutes.ts`.
 *
 * The reset form deliberately fires nothing on mount: the token is consumed
 * when the user submits, not when the page loads. That is what makes the
 * StrictMode trap documented in `lib/emailVerification.ts` unreachable here —
 * there is no mount-time request to duplicate or orphan, only a submit-time one
 * the user triggers.
 */

import { MIN_PASSWORD_LENGTH, passwordLengthError } from "@/lib/passwordPolicy";

/**
 * Re-exported for the callers that already import it from this module; the
 * value and the note about mirroring the server schema live in
 * `lib/passwordPolicy.ts`, which is the single source.
 */
export { MIN_PASSWORD_LENGTH };

/**
 * The API's generic 400 detail for an unknown, already-used, or expired token.
 *
 * The server must not say which of those applies (doing so would disclose
 * whether a token ever existed), so the SPA recognises the single message
 * rather than branching on an error code. Keep it byte-identical to the
 * backend copy — the page renders this text, it does not invent its own.
 */
export const PASSWORD_RESET_INVALID_MESSAGE =
  "This password reset link is invalid or has expired. Request a new one and try again.";

/**
 * Shown when a failure carries no usable message of its own (a non-Error throw,
 * an empty `message`). Never render `undefined` at a user.
 */
const FALLBACK_FAILURE_MESSAGE =
  "Something went wrong. Request a new link and try again.";

/** `"missing"` means no token was in the address, not that it was rejected. */
export type PasswordResetLinkState = "ready" | "missing";

/**
 * Which panel the reset page should render for the token in the address bar.
 *
 * Only two outcomes, because the token's *validity* is unknowable before it is
 * submitted: an invalid or expired token is answered with a 400 at submit time,
 * which is handled by {@link passwordResetFailureMessage}. A blank or absent
 * token, on the other hand, is a link that was never usable — there is nothing
 * to submit, so the page must say so instead of rendering a form that can only
 * fail.
 */
export function passwordResetLinkState(
  token: string | null | undefined,
): PasswordResetLinkState {
  return typeof token === "string" && token.trim().length > 0 ? "ready" : "missing";
}

/** Per-field messages keyed by the reset form's own field names. */
export interface NewPasswordErrors {
  password?: string;
  confirm?: string;
}

/**
 * Validate the reset form.
 *
 * The confirmation field is client-side only: it never leaves the browser, its
 * whole purpose is to catch a typo before the write. Messages are returned per
 * field so each one can sit under the input it belongs to.
 */
export function validateNewPassword(password: string, confirm: string): NewPasswordErrors {
  const errors: NewPasswordErrors = {};

  if (!password) errors.password = "Enter a new password.";
  else {
    const tooShort = passwordLengthError(password);
    if (tooShort) errors.password = tooShort;
  }

  if (confirm !== password) errors.confirm = "The two passwords don't match.";

  return errors;
}

/** Read a field off a thrown value without assuming it is an `Error`. */
function readField(value: unknown, key: string): unknown {
  if (!value || typeof value !== "object") return undefined;
  return (value as Record<string, unknown>)[key];
}

/**
 * The copy to show when a reset submission fails.
 *
 * Read structurally (`status`/`message` off the object) rather than with
 * `instanceof ApiError`, so a structurally identical error from a test double
 * behaves the same and this module does not depend on the API client class.
 *
 * A 400 from this endpoint always means "that link is dead" — the server gives
 * the same answer for unknown, already-used and expired tokens — so it is
 * collapsed onto {@link PASSWORD_RESET_INVALID_MESSAGE}. Recognising that
 * constant is also how the page knows to offer a new link instead of a
 * password box that is guaranteed to fail again. Anything else (a network
 * fault, an unexpected status) surfaces its own message, and a throw with no
 * usable message falls back to generic copy.
 */
export function passwordResetFailureMessage(err: unknown): string {
  const status = readField(err, "status");
  const message = readField(err, "message");

  if (status === 400 || message === PASSWORD_RESET_INVALID_MESSAGE) {
    return PASSWORD_RESET_INVALID_MESSAGE;
  }

  return typeof message === "string" && message ? message : FALLBACK_FAILURE_MESSAGE;
}
