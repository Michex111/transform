/**
 * Guards for the one place the client states the password minimum.
 *
 * The point of consolidating the constant and the message into
 * `lib/passwordPolicy.ts` was that a rule and the sentence explaining it cannot
 * drift. These tests are what makes that true rather than aspirational.
 */

import { describe, expect, it } from "vitest";
import {
  MIN_PASSWORD_LENGTH,
  passwordLengthError,
  passwordTooShortMessage,
} from "@/lib/passwordPolicy";

describe("MIN_PASSWORD_LENGTH", () => {
  it("mirrors the server schema", () => {
    // `UserCreateRequest.password` in `src/presentation/schemas/auth.py`.
    // Changing this breaks a test in three modules on purpose.
    expect(MIN_PASSWORD_LENGTH).toBe(8);
  });
});

describe("passwordTooShortMessage", () => {
  it("states the same length as the constant", () => {
    // The one-line guard: the message is built from the constant, so this can
    // only fail if someone writes a literal into it again.
    expect(passwordTooShortMessage()).toContain(String(MIN_PASSWORD_LENGTH));
  });

  it("reads as a sentence about the rule", () => {
    expect(passwordTooShortMessage()).toBe(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
  });
});

describe("passwordLengthError", () => {
  it("accepts exactly the minimum", () => {
    expect(passwordLengthError("x".repeat(MIN_PASSWORD_LENGTH))).toBeUndefined();
  });

  it("rejects one character under the minimum", () => {
    expect(passwordLengthError("x".repeat(MIN_PASSWORD_LENGTH - 1))).toBe(
      passwordTooShortMessage(),
    );
  });

  it("treats an empty value as too short, so callers must check emptiness first", () => {
    // Deliberate: the reset form says "Enter a new password." and sign-up says
    // "Enter a password.", and only the caller knows which. Both callers
    // therefore branch on `!value` before asking this helper, which is why it
    // never has to invent an "empty" message.
    expect(passwordLengthError("")).toBe(passwordTooShortMessage());
  });
});
