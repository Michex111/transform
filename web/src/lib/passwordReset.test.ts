/**
 * Tests for the password-reset page's decision logic.
 *
 * The repo has no DOM test library, so this module is where the reset flow's
 * rules live and where they are actually exercised: which panel a token maps to,
 * whether the two password fields are acceptable, and which copy explains a
 * rejected submission.
 */

import { describe, expect, it } from "vitest";
import {
  MIN_PASSWORD_LENGTH,
  PASSWORD_RESET_INVALID_MESSAGE,
  passwordResetFailureMessage,
  passwordResetLinkState,
  validateNewPassword,
} from "@/lib/passwordReset";

describe("contract copy", () => {
  it("pins the server's generic invalid-link detail verbatim", () => {
    // The page renders this text and recognises it, so a change on either side
    // must break a test rather than silently stop matching at runtime.
    expect(PASSWORD_RESET_INVALID_MESSAGE).toBe(
      "This password reset link is invalid or has expired. Request a new one and try again.",
    );
  });

  it("mirrors the server's minimum password length", () => {
    expect(MIN_PASSWORD_LENGTH).toBe(8);
  });
});

describe("passwordResetLinkState", () => {
  it("is ready when the address carries a token", () => {
    expect(passwordResetLinkState("tok-abc")).toBe("ready");
  });

  it.each([null, undefined, "", "   "])("is missing for %o", (token) => {
    // A blank token is a link that was never usable, not one that was rejected:
    // there is nothing to submit, so the form must not be rendered at all.
    expect(passwordResetLinkState(token)).toBe("missing");
  });
});

describe("validateNewPassword", () => {
  it("accepts a matching pair at exactly the minimum length", () => {
    const minimum = "12345678";
    expect(minimum).toHaveLength(MIN_PASSWORD_LENGTH);
    expect(validateNewPassword(minimum, minimum)).toEqual({});
  });

  it("rejects an empty password", () => {
    expect(validateNewPassword("", "")).toEqual({ password: "Enter a new password." });
  });

  it("rejects one character under the minimum", () => {
    const under = "1234567";
    expect(under).toHaveLength(MIN_PASSWORD_LENGTH - 1);
    expect(validateNewPassword(under, under)).toEqual({
      password: "Use at least 8 characters.",
    });
  });

  it("reports a mismatch on the confirmation field only", () => {
    // The password itself is fine; the typo is in the confirmation, and the
    // message belongs under the field the user can actually fix.
    expect(validateNewPassword("correct horse", "correct hors")).toEqual({
      confirm: "The two passwords don't match.",
    });
  });

  it("reports both fields when neither is usable", () => {
    expect(validateNewPassword("short", "")).toEqual({
      password: "Use at least 8 characters.",
      confirm: "The two passwords don't match.",
    });
  });
});

describe("passwordResetFailureMessage", () => {
  it("collapses a 400 onto the generic invalid-link copy", () => {
    // `readErrorBody` turns the plain-string detail into ApiError.message, so
    // this is the shape the page actually catches.
    const err = Object.assign(new Error(PASSWORD_RESET_INVALID_MESSAGE), { status: 400 });
    expect(passwordResetFailureMessage(err)).toBe(PASSWORD_RESET_INVALID_MESSAGE);
  });

  it("recognises the generic message even without a usable status", () => {
    // A proxy that rewrites the status but not the body must not change the copy.
    const err = Object.assign(new Error(PASSWORD_RESET_INVALID_MESSAGE), { status: 502 });
    expect(passwordResetFailureMessage(err)).toBe(PASSWORD_RESET_INVALID_MESSAGE);
  });

  it("surfaces the message of any other failure", () => {
    // A transport failure is not a dead token: the page must say what happened
    // instead of borrowing the invalid-link copy.
    expect(passwordResetFailureMessage(new Error("Failed to fetch"))).toBe("Failed to fetch");
    expect(
      passwordResetFailureMessage(Object.assign(new Error("Password too short"), { status: 422 })),
    ).toBe("Password too short");
  });

  it.each([
    ["a string", "boom"],
    ["null", null],
    ["undefined", undefined],
    ["a number", 42],
    ["an Error with no message", new Error("")],
  ])("falls back to generic copy for %s", (_label, value) => {
    // Never renders `undefined` or "[object Object]" at a user.
    expect(passwordResetFailureMessage(value)).toBe(
      "Something went wrong. Request a new link and try again.",
    );
  });
});
