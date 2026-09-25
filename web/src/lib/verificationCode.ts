/**
 * Rules for the SMS code field, kept out of React so they can be tested.
 *
 * Vitest runs with `environment: "node"`, so anything left in the component
 * would be untested — and the case that actually breaks here is a *paste* of
 * `"123 456"` or `"123-456"`, which a plain `maxLength` does not clean up.
 */

/** Digits in a phone verification code. */
export const PHONE_CODE_LENGTH = 6;

/**
 * Reduce anything typed or pasted to at most six digits.
 *
 * Stripping before truncating matters: `"123-456"` is seven characters, so a
 * naive `slice(0, 6)` would hand the API `"123-45"`.
 */
export function sanitizeCodeInput(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, PHONE_CODE_LENGTH);
}

/** True when the value has exactly the digits a code needs. */
export function isCompleteCode(value: string): boolean {
  return value.length === PHONE_CODE_LENGTH && /^\d+$/.test(value);
}
