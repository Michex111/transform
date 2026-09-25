// Tests for turning an API error body into copy a person can act on.
//
// The behaviour under test is the *placement* decision — which sentence goes
// under which input, and how several sentences are joined — because that logic
// used to live inside a React provider with no test environment (the repo has no
// jsdom), which is how the generic message survived.

import { describe, expect, it } from "vitest";
import {
  fieldNameFromLoc,
  joinValidationMessages,
  serverFieldErrors,
  validationErrorsFrom,
} from "@/lib/apiErrors";

describe("validationErrorsFrom", () => {
  it("reads FastAPI's validation array", () => {
    const items = validationErrorsFrom([
      { loc: ["body", "username"], type: "string_too_short", msg: "Username must be at least 3 characters." },
      { loc: ["body", "email"], type: "missing", msg: "Email is required." },
    ]);

    expect(items).toHaveLength(2);
    expect(items[0].msg).toBe("Username must be at least 3 characters.");
    expect(items[0].loc).toEqual(["body", "username"]);
    expect(items[1].type).toBe("missing");
  });

  it("is not fooled by the other `detail` shapes", () => {
    // A string detail and a structured object detail are both possible, so an
    // error handler must not treat either as a validation array.
    expect(validationErrorsFrom("Plain message.")).toEqual([]);
    expect(validationErrorsFrom({ code: "EMAIL_NOT_VERIFIED", message: "…" })).toEqual([]);
    expect(validationErrorsFrom(undefined)).toEqual([]);
    expect(validationErrorsFrom(null)).toEqual([]);
  });

  it("drops entries that carry no message", () => {
    // Rendering `undefined` in a form is worse than showing nothing.
    const items = validationErrorsFrom([{ loc: ["body", "x"] }, { msg: 42 }, { msg: "Kept." }]);

    expect(items).toHaveLength(1);
    expect(items[0].msg).toBe("Kept.");
  });
});

describe("joinValidationMessages", () => {
  it("separates full sentences as sentences", () => {
    // The bug this prevents: "…is required., Password must be…" — a comma
    // between two full stops reads as a typo.
    const joined = joinValidationMessages([
      "Username is required.",
      "Password must be at least 8 characters.",
    ]);

    expect(joined).toBe("Username is required. Password must be at least 8 characters.");
    expect(joined).not.toContain(".,");
  });

  it("comma-separates bare fragments", () => {
    // The shape the API used to send, and what a proxy or an older API can
    // still return. Two fragments joined by a space would be a run-on.
    expect(joinValidationMessages(["token must not be empty", "too long"])).toBe(
      "token must not be empty, too long",
    );
  });

  it("falls back to commas when only some entries are sentences", () => {
    // Mixed input has no unambiguous answer; comma separation is the one that
    // stays readable for the fragments.
    expect(joinValidationMessages(["Username is required.", "oops"])).toBe(
      "Username is required., oops",
    );
  });

  it("returns a single message untouched", () => {
    expect(joinValidationMessages(["Username must be at least 3 characters."])).toBe(
      "Username must be at least 3 characters.",
    );
  });

  it("handles the empty and whitespace-only cases", () => {
    expect(joinValidationMessages([])).toBe("");
    expect(joinValidationMessages(["  ", ""])).toBe("");
    expect(joinValidationMessages(["  Trimmed.  "])).toBe("Trimmed.");
  });
});

describe("fieldNameFromLoc", () => {
  it("takes the trailing field name", () => {
    expect(fieldNameFromLoc(["body", "username"])).toBe("username");
    expect(fieldNameFromLoc(["body", "items", 0, "count"])).toBe("count");
  });

  it("declines to name an array index", () => {
    // An item error belongs to the collection, not to "0".
    expect(fieldNameFromLoc(["body", "items", 0])).toBeUndefined();
    expect(fieldNameFromLoc(["body"])).toBeUndefined();
    expect(fieldNameFromLoc([])).toBeUndefined();
  });
});

describe("serverFieldErrors", () => {
  const FIELDS = ["username", "email", "password"] as const;

  it("places each message under the field it names", () => {
    const { fieldErrors, unplaced } = serverFieldErrors(
      [
        { loc: ["body", "username"], msg: "Username must be at least 3 characters." },
        { loc: ["body", "email"], msg: "Email is required." },
      ],
      FIELDS,
    );

    expect(fieldErrors).toEqual({
      username: "Username must be at least 3 characters.",
      email: "Email is required.",
    });
    expect(unplaced).toEqual([]);
  });

  it("keeps the first message when a field is named twice", () => {
    // A field has room for one line, and the API orders its errors by position.
    const { fieldErrors } = serverFieldErrors(
      [
        { loc: ["body", "username"], msg: "First." },
        { loc: ["body", "username"], msg: "Second." },
      ],
      FIELDS,
    );

    expect(fieldErrors.username).toBe("First.");
  });

  it("returns messages it cannot place instead of dropping them", () => {
    // Silently discarding the API's only explanation of what went wrong would
    // be worse than showing it generically.
    const { fieldErrors, unplaced } = serverFieldErrors(
      [
        { loc: ["body", "username"], msg: "Placed." },
        { loc: ["body"], msg: "The request body is not valid JSON." },
        { loc: ["body", "some_future_field"], msg: "Unplaced." },
      ],
      FIELDS,
    );

    expect(fieldErrors).toEqual({ username: "Placed." });
    expect(unplaced).toEqual(["The request body is not valid JSON.", "Unplaced."]);
  });

  it("handles an empty list", () => {
    expect(serverFieldErrors([], FIELDS)).toEqual({ fieldErrors: {}, unplaced: [] });
  });
});
