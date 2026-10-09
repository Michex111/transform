// The IME rule, which is the difference between a working and a broken Enter
// key for anyone typing with a candidate-based input method.
//
// The failure it prevents: pressing Enter to confirm a Japanese/Chinese/Korean
// candidate also submitting the message, so the assistant receives a
// half-finished word. It fires on every candidate, which makes the composer
// unusable rather than merely surprising — and it is invisible to anyone
// testing in a latin keyboard layout.

import { describe, expect, it } from "vitest";
import { isImeComposing } from "@/lib/keyboard";

describe("isImeComposing", () => {
  it("is false for an ordinary key event", () => {
    expect(isImeComposing({ isComposing: false, keyCode: 13 })).toBe(false);
  });

  it("is true while a composition is in progress", () => {
    expect(isImeComposing({ isComposing: true, keyCode: 13 })).toBe(true);
  });

  it("is true for the legacy 229 signal", () => {
    // Some IMEs send keyCode 229 on the keydown that starts a composition,
    // before `isComposing` has flipped. Checking only `isComposing` would let
    // that Enter through.
    expect(isImeComposing({ isComposing: false, keyCode: 229 })).toBe(true);
  });

  it("treats a missing composition flag as not composing", () => {
    // A browser (or a synthetic event in a test) may omit the field entirely;
    // defaulting to "composing" would swallow every Enter.
    expect(isImeComposing({ keyCode: 13 })).toBe(false);
    expect(isImeComposing({})).toBe(false);
  });

  it("is false for a missing event", () => {
    expect(isImeComposing(null)).toBe(false);
    expect(isImeComposing(undefined)).toBe(false);
  });
});
