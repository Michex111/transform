/**
 * Phone-number rules, kept out of React.
 *
 * Vitest runs with `environment: "node"` in this repo — there is no DOM — so
 * every decision the phone UI makes has to live here to be testable: is this a
 * usable number, what does this failure mean, is the attempt limit spent. The
 * components only render what these functions return.
 */

import {
  INVALID_PHONE_NUMBER,
  PHONE_CODE_ATTEMPTS_EXCEEDED,
  PHONE_CODE_EXPIRED,
  PHONE_CODE_INVALID,
  PHONE_IN_USE,
  SMS_DELIVERY_FAILED,
} from "@/api/types";

/**
 * E.164: a `+`, a non-zero country-code digit, then 7–14 more digits (15 total).
 *
 * A country code never starts with `0`, so allowing it would accept a number
 * that no carrier can route.
 */
const E164 = /^\+[1-9]\d{7,14}$/;

/**
 * Separators a person (or a chat app) may leave in a number.
 *
 * The non-breaking spaces are listed explicitly even though `\s` covers them,
 * because they are the ones that actually arrive from a paste and a future
 * engine change must not quietly start rejecting them.
 */
const PHONE_SEPARATORS = /[\s()\-.\u00a0\u2007\u202f]/g;

/**
 * How many wrong codes a code survives, mirroring the API's limit.
 *
 * Said out loud in the copy: a user reading "that code isn't right" with no idea
 * whether another try is allowed will assume the worst and start again.
 */
const PHONE_CODE_ATTEMPT_LIMIT = 5;

/** The copy shown for a number that is not in international format. */
export const INVALID_PHONE_NUMBER_MESSAGE =
  "Enter the number in international format, including the country code — for example +14155552671.";

/**
 * Clean what someone typed or pasted into something the API can look at.
 *
 * Deliberately does not decide validity: `isE164` is the gate. This only removes
 * presentation (`+1 (415) 555-2671` is how people write numbers) and repairs the
 * two prefixes that mean the same thing as `+`.
 */
export function normalizePhoneInput(raw: string): string {
  let cleaned = raw.replace(PHONE_SEPARATORS, "");

  // `00` is the international dialling prefix in most of the world; E.164 wants
  // `+`. `0011` (Australia) and similar national prefixes are not handled on
  // purpose — guessing wrong there would silently dial another country.
  if (cleaned.startsWith("00")) cleaned = `+${cleaned.slice(2)}`;

  // A number typed without any prefix is far more often missing its country
  // code than it is a local-only number, and `isE164` still rejects the result
  // when the assumption does not hold.
  if (/^\d+$/.test(cleaned)) cleaned = `+${cleaned}`;

  return cleaned;
}

/** True for a syntactically usable international number. */
export function isE164(value: string): boolean {
  return E164.test(value);
}

/** Chunks of three, merging a trailing single digit into the group before it. */
function groupDigits(digits: string): string {
  const groups = digits.match(/\d{1,3}/g) ?? [];
  const last = groups[groups.length - 1];
  if (groups.length > 1 && last.length === 1) {
    groups[groups.length - 2] += last;
    groups.pop();
  }
  return groups.join(" ");
}

/**
 * A lightly grouped number for display only — never fed back into an input.
 *
 * The stored value stays E.164; this is what a human reads back to check they
 * typed the right number. Only two shapes are grouped (`+1` NANP, and a
 * two-digit country code followed by 6–12 digits, which covers most of the
 * world); anything else is returned untouched rather than guessed at, because a
 * confidently wrong grouping is worse than no grouping.
 */
export function formatPhoneDisplay(e164: string | null | undefined): string {
  if (!e164) return "";
  const value = e164.trim();
  if (!isE164(value)) return value;

  const nanp = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(value);
  if (nanp) return `+1 ${nanp[1]} ${nanp[2]} ${nanp[3]}`;

  // An over-long or too-short `+1` number is not a NANP number. Grouping it as
  // if it had a two-digit country code would print a country the number is not
  // in, so leave it exactly as stored.
  if (value.startsWith("+1")) return value;

  const international = /^\+(\d{2})(\d{6,12})$/.exec(value);
  if (international) return `+${international[1]} ${groupDigits(international[2])}`;

  return value;
}

/**
 * The `code` an API error carries, if any.
 *
 * Reads the field structurally rather than with `instanceof ApiError` so this
 * module stays free of the API client (which pulls in the download and FENCR
 * helpers) and can be tested as plain logic.
 */
function errorCodeOf(err: unknown): string | undefined {
  if (!err || typeof err !== "object" || !("code" in err)) return undefined;
  const code = (err as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

/** True when a verification attempt ended because the attempt limit was spent. */
export function attemptsExhausted(err: unknown): boolean {
  return errorCodeOf(err) === PHONE_CODE_ATTEMPTS_EXCEEDED;
}

/**
 * Turn a phone/SMS failure into something the user can act on.
 *
 * Every branch says what happened *and* what to do next; the codes exist on the
 * wire precisely because the HTTP status cannot tell these apart (a 400 covers a
 * malformed number, a wrong code, and an expired one).
 */
export function phoneErrorFor(err: unknown): string {
  switch (errorCodeOf(err)) {
    case INVALID_PHONE_NUMBER:
      return INVALID_PHONE_NUMBER_MESSAGE;
    case PHONE_IN_USE:
      return "That number is already verified on another account. Use a different number, or remove it from the other account first.";
    case SMS_DELIVERY_FAILED:
      return "We couldn't send the text just now. Try again in a moment.";
    case PHONE_CODE_INVALID:
      return `That code isn't correct. Check the most recent message — you can try up to ${PHONE_CODE_ATTEMPT_LIMIT} times per code.`;
    case PHONE_CODE_EXPIRED:
      return "That code has expired. Request a new one.";
    case PHONE_CODE_ATTEMPTS_EXCEEDED:
      return "Too many incorrect attempts. Request a new code to keep going.";
    default:
      // No code (an older API, or a proxy's HTML error page) — the server's own
      // message is still more specific than anything we could invent.
      return err instanceof Error && err.message
        ? err.message
        : "Something went wrong. Please try again.";
  }
}
