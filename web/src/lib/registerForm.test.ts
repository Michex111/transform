// Tests for the sign-up form's local rules.
//
// These exist so a mistake is answered in the form instead of after a round
// trip. The cases that matter are the ones the markup cannot catch itself: a
// whitespace-only value passes the browser's `required` check, and a short
// username has no native equivalent — a two-character one used to reach the API
// and come back as "String should have at least 3 characters".

import { describe, expect, it } from "vitest";
import {
  MIN_PASSWORD_LENGTH,
  MIN_USERNAME_LENGTH,
  registrationServerErrors,
  validateRegistration,
  type RegistrationValues,
} from "@/lib/registerForm";

function values(overrides: Partial<RegistrationValues> = {}): RegistrationValues {
  return {
    firstName: "Ada",
    lastName: "Lovelace",
    username: "ada",
    email: "ada@example.com",
    password: "Sup3rSecret",
    confirm: "Sup3rSecret",
    ...overrides,
  };
}

describe("validateRegistration", () => {
  it("accepts a well-formed form", () => {
    expect(validateRegistration(values())).toEqual({});
  });

  it("names the username and the rule when it is too short", () => {
    // The reported defect: this used to be discovered only by the API, which
    // answered with a sentence about a "String".
    const errors = validateRegistration(values({ username: "ab" }));

    expect(errors.username).toBe(`Use at least ${MIN_USERNAME_LENGTH} characters.`);
    expect(errors.password).toBeUndefined();
  });

  it("accepts a username of exactly the minimum length", () => {
    expect(validateRegistration(values({ username: "abc" })).username).toBeUndefined();
  });

  it("rejects a username of one character", () => {
    expect(validateRegistration(values({ username: "a" })).username).toBe(
      `Use at least ${MIN_USERNAME_LENGTH} characters.`,
    );
  });

  it("asks for a missing or blank username", () => {
    expect(validateRegistration(values({ username: "" })).username).toBe("Enter a username.");
    // The browser treats " " as a value, so `required` alone lets this through
    // and the account would be created with a blank username.
    expect(validateRegistration(values({ username: "   " })).username).toBe("Enter a username.");
  });

  it("asks for each missing name", () => {
    const errors = validateRegistration(values({ firstName: "  ", lastName: "" }));

    expect(errors.first_name).toBe("Enter your first name.");
    expect(errors.last_name).toBe("Enter your last name.");
  });

  it("measures the username after trimming", () => {
    // " ab " is two characters of username, so a padded value must not pass.
    expect(validateRegistration(values({ username: " ab " })).username).toBe(
      `Use at least ${MIN_USERNAME_LENGTH} characters.`,
    );
  });

  it("asks for a missing email", () => {
    expect(validateRegistration(values({ email: " " })).email).toBe("Enter your email address.");
  });

  it("enforces the password length", () => {
    const short = "short";
    const errors = validateRegistration(values({ password: short, confirm: short }));

    expect(errors.password).toBe(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
    // A short password must not ALSO report a mismatch for the same value —
    // that would be two messages for one mistake.
    expect(errors.confirm).toBeUndefined();
  });

  it("accepts a password of exactly the minimum length", () => {
    const exact = "12345678";
    expect(
      validateRegistration(values({ password: exact, confirm: exact })).password,
    ).toBeUndefined();
  });

  it("reports a password mismatch on the confirmation field", () => {
    const errors = validateRegistration(values({ confirm: "Sup3rSecret!" }));

    expect(errors.confirm).toBe("The two passwords don't match.");
    expect(errors.password).toBeUndefined();
  });

  it("reports every problem at once", () => {
    // A form should not reveal its mistakes one round trip at a time.
    const errors = validateRegistration({
      firstName: "",
      lastName: "",
      username: "a",
      email: "",
      password: "x",
      confirm: "y",
    });

    expect(Object.keys(errors).sort()).toEqual(
      ["email", "first_name", "last_name", "password", "username"].sort(),
    );
  });
});

describe("registrationServerErrors", () => {
  it("places an API validation message under the field it names", () => {
    // What the API now sends for a two-character username.
    const { fieldErrors, unplaced } = registrationServerErrors([
      {
        loc: ["body", "username"],
        type: "string_too_short",
        msg: "Username must be at least 3 characters.",
      },
    ]);

    expect(fieldErrors).toEqual({ username: "Username must be at least 3 characters." });
    expect(unplaced).toEqual([]);
  });

  it("maps the confirm field and the name fields", () => {
    const { fieldErrors } = registrationServerErrors([
      { loc: ["body", "first_name"], msg: "First name is required." },
      { loc: ["body", "last_name"], msg: "Last name is required." },
    ]);

    expect(fieldErrors.first_name).toBe("First name is required.");
    expect(fieldErrors.last_name).toBe("Last name is required.");
  });

  it("hands back anything the form has no field for", () => {
    const { fieldErrors, unplaced } = registrationServerErrors([
      { loc: ["body"], msg: "The request body is not valid JSON." },
    ]);

    expect(fieldErrors).toEqual({});
    expect(unplaced).toEqual(["The request body is not valid JSON."]);
  });

  it("handles a 422 with no detail array", () => {
    expect(registrationServerErrors([])).toEqual({ fieldErrors: {}, unplaced: [] });
  });
});
