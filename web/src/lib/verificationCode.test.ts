// The SMS code field's rules. The paste cases are the ones that actually break
// in a browser, so they lead.

import { describe, expect, it } from "vitest";
import { PHONE_CODE_LENGTH, isCompleteCode, sanitizeCodeInput } from "@/lib/verificationCode";

describe("PHONE_CODE_LENGTH", () => {
  it("is six, matching the API", () => {
    expect(PHONE_CODE_LENGTH).toBe(6);
  });
});

describe("sanitizeCodeInput", () => {
  it("accepts a pasted code with a separating space", () => {
    expect(sanitizeCodeInput("123 456")).toBe("123456");
  });

  it("accepts a pasted code with a hyphen", () => {
    expect(sanitizeCodeInput("123-456")).toBe("123456");
  });

  it("keeps a long paste down to the code length", () => {
    // Stripping happens before truncating: a naive `slice(0, 6)` on "123-456"
    // would keep the hyphen and hand the API "123-45".
    expect(sanitizeCodeInput("123-4567")).toBe("123456");
    expect(sanitizeCodeInput("123 456 789")).toBe("123456");
  });

  it("drops non-digits", () => {
    expect(sanitizeCodeInput("12a34")).toBe("1234");
    expect(sanitizeCodeInput("abc")).toBe("");
    expect(sanitizeCodeInput("")).toBe("");
  });

  it("drops the non-breaking spaces a paste may bring", () => {
    expect(sanitizeCodeInput("123\u00a0456")).toBe("123456");
  });
});

describe("isCompleteCode", () => {
  it("is true for exactly six digits", () => {
    expect(isCompleteCode("123456")).toBe(true);
    expect(isCompleteCode("000000")).toBe(true);
  });

  it("is false for anything else", () => {
    expect(isCompleteCode("")).toBe(false);
    expect(isCompleteCode("12345")).toBe(false);
    expect(isCompleteCode("1234567")).toBe(false);
    expect(isCompleteCode("12345a")).toBe(false);
    expect(isCompleteCode("12 456")).toBe(false);
  });
});
