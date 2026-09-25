/**
 * The client's single copy of the password minimum, and of the sentence that
 * states it.
 *
 * `MIN_PASSWORD_LENGTH` mirrors `UserCreateRequest.password` in
 * `src/presentation/schemas/auth.py`. The duplication is deliberate: telling
 * the user the rule before the round trip is better than a generic 422 toast,
 * and the field hints render from this constant so the rule is visible while
 * typing. It is **not** a security control — the API validates every password
 * again and is the only authority on the rule.
 *
 * Keep it in step with the server: a client that refuses a value the API would
 * accept is a worse bug than the generic message this check exists to prevent.
 * If the server relaxes the minimum, relax it here in the same change.
 *
 * The number used to be defined three times (`lib/registerForm.ts`,
 * `lib/passwordReset.ts`, `pages/app/settings/PasswordSection.tsx`), each with
 * its own copy of the mirroring note above. Everything imports it from here
 * now (the two `lib/` modules re-export it so their existing importers keep
 * working), because three copies of a rule and its explanation is exactly how
 * they drift apart.
 */

export const MIN_PASSWORD_LENGTH = 8;

/**
 * The too-short message, built from the constant rather than written out.
 *
 * That is the point of having it here: a message that hard-coded "8" would
 * keep claiming 8 after the constant moved.
 */
export function passwordTooShortMessage(): string {
  return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
}

/**
 * `undefined` when `value` meets the minimum length.
 *
 * Emptiness is deliberately not this helper's business: the reset form says
 * "Enter a new password." and the sign-up form says "Enter a password.", and
 * only the caller knows which it is rendering.
 */
export function passwordLengthError(value: string): string | undefined {
  return value.length < MIN_PASSWORD_LENGTH ? passwordTooShortMessage() : undefined;
}
