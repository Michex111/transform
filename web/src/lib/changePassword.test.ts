/**
 * Tests for the standalone "change password" card's rules.
 *
 * The repo has no DOM test library, so this module is where the card's
 * decisions live and where they are actually exercised: which field a bad new
 * password belongs to, and whether a failed submission is a field problem (a
 * wrong old password) or a whole-dialog one.
 */

import { describe, expect, it } from "vitest";
import { INVALID_PASSWORD } from "@/api/types";
import { MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";
import {
  CHANGE_PASSWORD_FALLBACK_MESSAGE,
  CURRENT_PASSWORD_INCORRECT_MESSAGE,
  CURRENT_PASSWORD_REQUIRED_MESSAGE,
  currentPasswordFailureFor,
  isInvalidPasswordError,
  validateCurrentPassword,
  validateNewPasswordPair,
} from "@/lib/changePassword";

describe("validateNewPasswordPair", () => {
  it("accepts a matching pair at exactly the minimum length", () => {
    const minimum = "x".repeat(MIN_PASSWORD_LENGTH);
    expect(minimum).toHaveLength(MIN_PASSWORD_LENGTH);
    expect(validateNewPasswordPair(minimum, minimum)).toEqual({});
  });

  it("rejects one character under the minimum", () => {
    const under = "x".repeat(MIN_PASSWORD_LENGTH - 1);
    expect(validateNewPasswordPair(under, under)).toEqual({
      password: `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
    });
  });

  it("rejects an empty password", () => {
    expect(validateNewPasswordPair("", "")).toEqual({ password: "Enter a new password." });
  });

  it("reports a mismatch on the confirmation field only", () => {
    // The password itself is fine; the typo is in the confirmation, so the
    // message belongs under the field the user can actually fix.
    expect(validateNewPasswordPair("correct horse", "correct hors")).toEqual({
      confirm: "The two passwords don't match.",
    });
  });

  it("suppresses the mismatch when the password itself is invalid", () => {
    // One mistake, one message: a seven-character password that was not
    // retyped is a length problem, and saying "they don't match" as well would
    // point at a value the user is going to replace anyway.
    expect(validateNewPasswordPair("short", "")).toEqual({
      password: `Use at least ${MIN_PASSWORD_LENGTH} characters.`,
    });
    expect(validateNewPasswordPair("", "anything")).toEqual({
      password: "Enter a new password.",
    });
  });
});

describe("validateCurrentPassword", () => {
  it("accepts anything the user typed", () => {
    expect(validateCurrentPassword("hunter2")).toBeUndefined();
  });

  it("refuses an empty box rather than posting it", () => {
    expect(validateCurrentPassword("")).toBe(CURRENT_PASSWORD_REQUIRED_MESSAGE);
  });
});

describe("isInvalidPasswordError", () => {
  it("recognises the API's structured code", () => {
    expect(isInvalidPasswordError({ code: INVALID_PASSWORD, message: "nope" })).toBe(true);
  });

  it("rejects another code, a plain Error, and non-objects", () => {
    expect(isInvalidPasswordError({ code: "SOMETHING_ELSE" })).toBe(false);
    expect(isInvalidPasswordError(new Error("Network down"))).toBe(false);
    expect(isInvalidPasswordError("INVALID_PASSWORD")).toBe(false);
    expect(isInvalidPasswordError(null)).toBe(false);
    expect(isInvalidPasswordError(undefined)).toBe(false);
    expect(isInvalidPasswordError(42)).toBe(false);
  });
});

describe("currentPasswordFailureFor", () => {
  it("puts the wrong-old-password message under that field", () => {
    const failure = currentPasswordFailureFor({ code: INVALID_PASSWORD, message: "Invalid" });
    expect(failure.field).toBe("current-password");
    expect(failure.message).toBe(CURRENT_PASSWORD_INCORRECT_MESSAGE);
  });

  it("surfaces any other failure inline, with no field attached", () => {
    const failure = currentPasswordFailureFor(new Error("Failed to fetch"));
    expect(failure.field).toBeUndefined();
    expect(failure.message).toBe("Failed to fetch");
  });

  it.each([
    ["a string", "boom"],
    ["null", null],
    ["undefined", undefined],
    ["a number", 42],
    ["an Error with no message", new Error("")],
  ])("falls back to generic copy for %s", (_label, value) => {
    // Never renders `undefined`, "[object Object]", or the submitted password
    // (which is never on the error object to begin with) at a user.
    const failure = currentPasswordFailureFor(value);
    expect(failure.field).toBeUndefined();
    expect(failure.message).toBe(CHANGE_PASSWORD_FALLBACK_MESSAGE);
  });
});
