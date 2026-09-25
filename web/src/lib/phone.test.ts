// Rules for the SMS/phone section: what is a usable number, how it is shown
// back, and what each failure code means. Everything here is pure so it can be
// tested without a DOM — which matters, because this repo's Vitest environment
// is `node`.

import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import {
  INVALID_PHONE_NUMBER,
  PHONE_CODE_ATTEMPTS_EXCEEDED,
  PHONE_CODE_EXPIRED,
  PHONE_CODE_INVALID,
  PHONE_IN_USE,
  SMS_DELIVERY_FAILED,
} from "@/api/types";
import {
  INVALID_PHONE_NUMBER_MESSAGE,
  attemptsExhausted,
  formatPhoneDisplay,
  isE164,
  normalizePhoneInput,
  phoneErrorFor,
} from "@/lib/phone";

describe("normalizePhoneInput", () => {
  it("strips the separators people type", () => {
    expect(normalizePhoneInput("+1 415 555 2671")).toBe("+14155552671");
    expect(normalizePhoneInput("(415) 555-2671")).toBe("+4155552671");
    expect(normalizePhoneInput("415.555.2671")).toBe("+4155552671");
  });

  it("strips the non-breaking spaces that arrive from a paste", () => {
    // `\u00a0` is what a web form or a chat client hands over, and it is
    // invisible — so a number "with no spaces" can still carry three of them.
    expect(normalizePhoneInput("+1\u00a0415\u00a0555\u00a02671")).toBe("+14155552671");
    expect(normalizePhoneInput("+1\u202f415\u202f555\u202f2671")).toBe("+14155552671");
  });

  it("converts the 00 international prefix to +", () => {
    expect(normalizePhoneInput("00 44 20 7183 8750")).toBe("+442071838750");
  });

  it("prefixes a bare run of digits with +", () => {
    expect(normalizePhoneInput("14155552671")).toBe("+14155552671");
  });

  it("leaves anything that is not a number alone", () => {
    expect(normalizePhoneInput("")).toBe("");
    expect(normalizePhoneInput("not a number")).toBe("notanumber");
  });

  it("does not pretend a cleaned value is valid", () => {
    // The whole point of separating cleaning from validating: this comes back
    // clean *and* unusable, and `isE164` is what says so.
    expect(normalizePhoneInput("123")).toBe("+123");
    expect(isE164(normalizePhoneInput("123"))).toBe(false);
  });
});

describe("isE164", () => {
  it("accepts numbers the API will take", () => {
    expect(isE164("+14155552671")).toBe(true);
    expect(isE164("+442071838750")).toBe(true);
    // Shortest permitted shape: `+` and a country code, then 7 more digits.
    expect(isE164("+12345678")).toBe(true);
  });

  it("rejects the shapes that are not E.164", () => {
    expect(isE164("")).toBe(false);
    expect(isE164("4155552671")).toBe(false);
    expect(isE164("+14155552671 ")).toBe(false);
    expect(isE164("++14155552671")).toBe(false);
    // E.164 allows 15 digits in total; this is 16.
    expect(isE164("+1234567890123456")).toBe(false);
    // A country code never starts with 0 — no carrier could route it.
    expect(isE164("+04155552671")).toBe(false);
    expect(isE164("+1234567")).toBe(false);
  });
});

describe("formatPhoneDisplay", () => {
  it("groups a +1 number the way people read it", () => {
    expect(formatPhoneDisplay("+14155552671")).toBe("+1 415 555 2671");
  });

  it("groups a two-digit country code and absorbs a stray trailing digit", () => {
    // 10 national digits would otherwise split 3/3/3/1, leaving an orphan.
    expect(formatPhoneDisplay("+442071838750")).toBe("+44 207 183 8750");
    expect(formatPhoneDisplay("+33612345678")).toBe("+33 612 345 678");
  });

  it("returns an unknown shape unchanged rather than guessing", () => {
    // A confidently wrong grouping is worse than no grouping: it prints a
    // country the number is not in.
    expect(formatPhoneDisplay("+155512345678")).toBe("+155512345678");
    expect(formatPhoneDisplay("+1234567890123456")).toBe("+1234567890123456");
    expect(formatPhoneDisplay("4155552671")).toBe("4155552671");
  });

  it("returns nothing for an absent number", () => {
    expect(formatPhoneDisplay(null)).toBe("");
    expect(formatPhoneDisplay(undefined)).toBe("");
  });
});

describe("phoneErrorFor", () => {
  function apiError(code?: string, message = "from the server") {
    return new ApiError(message, 400, code);
  }

  it("explains the expected format for an invalid number", () => {
    const message = phoneErrorFor(apiError(INVALID_PHONE_NUMBER));
    expect(message).toBe(INVALID_PHONE_NUMBER_MESSAGE);
    expect(message).toContain("+14155552671");
    expect(message).toContain("country code");
  });

  it("says a number is already taken by another account", () => {
    expect(phoneErrorFor(apiError(PHONE_IN_USE))).toContain(
      "already verified on another account",
    );
  });

  it("tells the user to retry when the text could not be sent", () => {
    const message = phoneErrorFor(apiError(SMS_DELIVERY_FAILED));
    expect(message).toContain("couldn't send the text");
    expect(message).toContain("moment");
  });

  it("names the attempt limit for a wrong code", () => {
    // Without the limit, "that code isn't right" leaves the user unsure whether
    // another try is even allowed.
    const message = phoneErrorFor(apiError(PHONE_CODE_INVALID));
    expect(message).toContain("isn't correct");
    expect(message).toContain("5");
  });

  it("asks for a new code when the old one expired", () => {
    expect(phoneErrorFor(apiError(PHONE_CODE_EXPIRED))).toContain("expired");
  });

  it("asks for a new code when the attempts are spent", () => {
    expect(phoneErrorFor(apiError(PHONE_CODE_ATTEMPTS_EXCEEDED))).toContain(
      "Too many incorrect attempts",
    );
  });

  it("falls back to the server's message when the code is unknown", () => {
    // An older API sends no code at all; "something went wrong" is worse than
    // whatever it did say.
    expect(phoneErrorFor(apiError(undefined, "Number is already in use"))).toBe(
      "Number is already in use",
    );
    expect(phoneErrorFor(apiError("SOMETHING_ELSE", "Nope"))).toBe("Nope");
  });

  it("falls back to the message of a plain Error", () => {
    expect(phoneErrorFor(new Error("Failed to fetch"))).toBe("Failed to fetch");
  });

  it("has a generic fallback for anything that is not an Error", () => {
    const generic = "Something went wrong. Please try again.";
    expect(phoneErrorFor("boom")).toBe(generic);
    expect(phoneErrorFor(null)).toBe(generic);
    expect(phoneErrorFor(undefined)).toBe(generic);
    expect(phoneErrorFor(new Error(""))).toBe(generic);
  });
});

describe("attemptsExhausted", () => {
  it("is true only for the exhausted-attempts code", () => {
    expect(attemptsExhausted(new ApiError("nope", 429, PHONE_CODE_ATTEMPTS_EXCEEDED))).toBe(true);
    expect(attemptsExhausted(new ApiError("nope", 400, PHONE_CODE_INVALID))).toBe(false);
    expect(attemptsExhausted(new ApiError("nope", 400))).toBe(false);
    expect(attemptsExhausted(new Error("network"))).toBe(false);
    expect(attemptsExhausted(null)).toBe(false);
  });
});
