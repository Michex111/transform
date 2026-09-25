/**
 * Rules for the standalone "change password" card, kept out of React so they
 * can be tested.
 *
 * The repo's vitest environment is `node` (no jsdom, no testing-library), so
 * every user-visible decision the card makes — which field is wrong, whether a
 * failure belongs under a field or is a whole-form problem, and what either of
 * those says — lives here. The component is left as a rendering layer. Same
 * workaround as `lib/passwordReset.ts` (whose `validateNewPassword` this
 * closely mirrors) and `lib/jobStore.ts`.
 *
 * The flow this supports is two-step on purpose: the resting card only asks
 * for the new password, and the *current* password is collected in a
 * confirmation dialog before anything is sent. The API call therefore happens
 * once, from inside the dialog — see `pages/app/settings/PasswordSection.tsx`.
 */

import { INVALID_PASSWORD } from "@/api/types";
import { passwordLengthError } from "@/lib/passwordPolicy";

/** Per-field messages under the two new-password inputs. */
export interface ChangePasswordErrors {
  password?: string;
  confirm?: string;
}

/**
 * Validate the resting form's two fields.
 *
 * The confirmation field is client-side only: it never leaves the browser, its
 * whole purpose is to catch a typo before the write.
 *
 * The mismatch is reported **only** when the password itself is acceptable
 * (the rule `lib/registerForm.ts` uses). A seven-character password that does
 * not equal its confirmation is one mistake, and the user needs to be told
 * about the length of the thing they must fix, not handed a second message
 * about a value that is going to be retyped anyway.
 */
export function validateNewPasswordPair(next: string, confirm: string): ChangePasswordErrors {
  const errors: ChangePasswordErrors = {};

  if (!next) {
    errors.password = "Enter a new password.";
  } else {
    const tooShort = passwordLengthError(next);
    if (tooShort) errors.password = tooShort;
  }

  if (!errors.password && confirm !== next) errors.confirm = "The two passwords don't match.";

  return errors;
}

/** The message the dialog's field shows when the old password is wrong. */
export const CURRENT_PASSWORD_INCORRECT_MESSAGE = "Current password is incorrect.";

/** Shown when the current-password box is submitted empty. */
export const CURRENT_PASSWORD_REQUIRED_MESSAGE = "Enter your current password.";

/**
 * Shown for a failure that says nothing useful of its own (a non-`Error`
 * throw, a dropped connection with no body). Never render `undefined` at a
 * user.
 */
export const CHANGE_PASSWORD_FALLBACK_MESSAGE = "Could not change your password. Try again.";

/** `undefined` when the dialog's one field may be submitted. */
export function validateCurrentPassword(value: string): string | undefined {
  return value.length === 0 ? CURRENT_PASSWORD_REQUIRED_MESSAGE : undefined;
}

/**
 * True when the API refused the change because the *current* password was
 * wrong — `ApiError.code === "INVALID_PASSWORD"` in the structured `detail`.
 *
 * Read structurally rather than with `instanceof ApiError`, so this module does
 * not depend on the API client class and a structurally identical error from a
 * test double behaves the same way.
 */
export function isInvalidPasswordError(err: unknown): boolean {
  if (!err || typeof err !== "object" || !("code" in err)) return false;
  return (err as { code?: unknown }).code === INVALID_PASSWORD;
}

/** Read a string field off a thrown value without assuming it is an `Error`. */
function readMessage(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const message = (value as { message?: unknown }).message;
  return typeof message === "string" && message ? message : undefined;
}

/**
 * Where a failed submission's message belongs.
 *
 * `field` is `"current-password"` for the one failure the user can fix by
 * retyping in this dialog: the server's `INVALID_PASSWORD`. It is absent for
 * everything else (a 500, a timeout, a validation error about the *new*
 * password), which the dialog shows as an inline form-level message instead —
 * pinning those under the old-password box would point the user at the wrong
 * field.
 *
 * The text lives here rather than in the component so the rule and the copy
 * that explains it cannot drift apart, and so a test can pin it.
 */
export interface ChangePasswordFailure {
  field?: "current-password";
  message: string;
}

export function currentPasswordFailureFor(err: unknown): ChangePasswordFailure {
  if (isInvalidPasswordError(err)) {
    return { field: "current-password", message: CURRENT_PASSWORD_INCORRECT_MESSAGE };
  }
  return { message: readMessage(err) ?? CHANGE_PASSWORD_FALLBACK_MESSAGE };
}
