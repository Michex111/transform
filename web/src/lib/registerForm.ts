/**
 * Client-side rules for the sign-up form.
 *
 * These exist so the common mistake is answered **before** the request: the
 * user is told which box is wrong, in the place they are looking, instead of
 * waiting for a round trip and reading about it in a toast. They are not a
 * security control — the API validates every field again, and it is the only
 * authority on the rules.
 *
 * **The duplication is deliberate and must be kept in step.** `MIN_USERNAME_LENGTH`
 * mirrors `UserCreateRequest.username` (`min_length=3`) in
 * `src/presentation/schemas/auth.py`. If the server ever relaxes it, this check
 * must relax with it — a client that refuses a value the API would accept is a
 * worse bug than the generic message it was added to prevent. The password
 * minimum is the same trade and is now defined once, in
 * `lib/passwordPolicy.ts`, which carries the mirroring note for
 * `UserCreateRequest.password` and builds the message that states the rule.
 *
 * Pure and DOM-free so it is testable in the repo's `node` test environment,
 * which has no jsdom — the established pattern here (`lib/jobStore.ts`,
 * `lib/passwordReset.ts`).
 */

import { serverFieldErrors, type ValidationErrorItem } from "@/lib/apiErrors";
import { MIN_PASSWORD_LENGTH, passwordLengthError } from "@/lib/passwordPolicy";

/** Mirrors `UserCreateRequest.username` in `src/presentation/schemas/auth.py`. */
export const MIN_USERNAME_LENGTH = 3;

/**
 * Re-exported for the callers that already import it from this module
 * (`RegisterPage` and its tests); the value lives in `lib/passwordPolicy.ts`.
 */
export { MIN_PASSWORD_LENGTH };

/** The inputs this form can render an error under. */
export const REGISTER_FIELDS = [
  "first_name",
  "last_name",
  "username",
  "email",
  "password",
  "confirm",
] as const;

export type RegisterField = (typeof REGISTER_FIELDS)[number];

export type RegisterFieldErrors = Partial<Record<RegisterField, string>>;

export interface RegistrationValues {
  firstName: string;
  lastName: string;
  username: string;
  email: string;
  password: string;
  confirm: string;
}

/**
 * Check the form locally.
 *
 * Whitespace-only input counts as empty — every field is `required` in the
 * markup, but the browser considers `" "` a value, so a name of spaces reaches
 * this function and would otherwise create an account with a blank name.
 *
 * Returns an empty object when the form may be submitted. Messages are terse
 * because they render directly under the labelled field, which supplies the
 * context: "Use at least 3 characters." under a visible "Username" label reads
 * better than naming the field twice.
 */
export function validateRegistration(values: RegistrationValues): RegisterFieldErrors {
  const errors: RegisterFieldErrors = {};

  if (!values.firstName.trim()) errors.first_name = "Enter your first name.";
  if (!values.lastName.trim()) errors.last_name = "Enter your last name.";

  const username = values.username.trim();
  if (!username) errors.username = "Enter a username.";
  else if (username.length < MIN_USERNAME_LENGTH)
    errors.username = `Use at least ${MIN_USERNAME_LENGTH} characters.`;

  if (!values.email.trim()) errors.email = "Enter your email address.";

  if (!values.password) errors.password = "Enter a password.";
  else {
    const tooShort = passwordLengthError(values.password);
    if (tooShort) errors.password = tooShort;
  }

  // Only meaningful once the password itself is valid, so a short password does
  // not also produce a confusing "they don't match" for the same value.
  if (!errors.password && values.confirm !== values.password)
    errors.confirm = "The two passwords don't match.";

  return errors;
}

/**
 * Place the API's validation errors on the fields they name.
 *
 * The API used to answer a two-character username with "String should have at
 * least 3 characters" — a sentence about a Python type, with the field never
 * named. It now sends "Username must be at least 3 characters." plus a `loc`
 * path, which is what lets this put the message under the box the user typed
 * into instead of in a toast. Anything that cannot be attributed (a
 * whole-body error, a field this form does not have) comes back in `unplaced`
 * so the caller can still show it.
 */
export function registrationServerErrors(items: ValidationErrorItem[]): {
  fieldErrors: RegisterFieldErrors;
  unplaced: string[];
} {
  return serverFieldErrors(items, REGISTER_FIELDS);
}
