// Tests for the chat-height arithmetic.
//
// This is the part of the iOS-keyboard fix that can actually be pinned in the
// Node environment: the hook reads `window.visualViewport`, which vitest has no
// layout engine to fake meaningfully, but the decision it feeds — how much room
// is left, and what to do when there is almost none — is pure and is tested
// here.

import { describe, expect, it } from "vitest";
import { availableChatHeight, viewportHeightFrom } from "@/lib/chatViewport";

describe("availableChatHeight", () => {
  it("subtracts the reserved chrome from the viewport", () => {
    expect(availableChatHeight({ viewportHeight: 874, reservedPx: 156 })).toBe(718);
  });

  it("honours a floor", () => {
    expect(availableChatHeight({ viewportHeight: 400, reservedPx: 156, minPx: 384 })).toBe(384);
  });

  it("collapses to zero on a viewport shorter than its chrome", () => {
    // The keyboard has eaten the screen: there is no room for the column, and a
    // negative inline height would be worse than none.
    expect(availableChatHeight({ viewportHeight: 120, reservedPx: 156 })).toBe(0);
    expect(availableChatHeight({ viewportHeight: 120, reservedPx: 156, minPx: 0 })).toBe(0);
  });

  it("treats nonsense measurements as zero rather than propagating them", () => {
    expect(availableChatHeight({ viewportHeight: Number.NaN, reservedPx: 40 })).toBe(0);
    expect(availableChatHeight({ viewportHeight: 800, reservedPx: Number.NaN })).toBe(800);
    expect(availableChatHeight({ viewportHeight: -50, reservedPx: -20 })).toBe(0);
    expect(availableChatHeight({ viewportHeight: 800, reservedPx: 200, minPx: -10 })).toBe(600);
  });

  it("rounds fractional layout measurements to whole pixels", () => {
    expect(availableChatHeight({ viewportHeight: 874.6, reservedPx: 155.2 })).toBe(719);
  });
});

describe("viewportHeightFrom", () => {
  it("prefers the visual viewport", () => {
    expect(viewportHeightFrom({ height: 512 }, 874)).toBe(512);
  });

  it("falls back to innerHeight when there is no visual viewport", () => {
    expect(viewportHeightFrom(null, 874)).toBe(874);
    expect(viewportHeightFrom(undefined, 620)).toBe(620);
  });

  it("ignores a zero or non-finite visual viewport height", () => {
    expect(viewportHeightFrom({ height: 0 }, 874)).toBe(874);
    expect(viewportHeightFrom({ height: Number.NaN }, 874)).toBe(874);
  });

  it("sanitises a nonsense fallback", () => {
    expect(viewportHeightFrom(null, Number.NaN)).toBe(0);
  });
});
