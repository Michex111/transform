// The rule behind the transcript's bottom-following.
//
// Pure and DOM-free so it can be tested without a layout engine: when it is
// wrong, nothing about a screenshot looks different — the viewport is pinned
// either way — but the transcript either drags itself down while someone reads
// an earlier answer, or stops following a live answer they are waiting on.

import { describe, expect, it } from "vitest";
import { FOLLOW_THRESHOLD_PX, isNearBottom } from "@/lib/scrollFollow";

/** A container 400px tall with `content` px of scrollable content, scrolled `top`. */
function metrics(scrollTop: number, scrollHeight: number, clientHeight = 400) {
  return { scrollTop, scrollHeight, clientHeight };
}

describe("isNearBottom", () => {
  it("is pinned when the newest content is in view", () => {
    expect(isNearBottom(metrics(600, 1000))).toBe(true);
  });

  it("is not pinned once the reader has scrolled up past the threshold", () => {
    expect(isNearBottom(metrics(100, 1000))).toBe(false);
  });

  it("tolerates the sub-pixel gap at the true bottom", () => {
    // `scrollHeight - scrollTop - clientHeight` is rarely exactly 0 after a
    // layout pass, and a zero threshold would unpin the transcript while the
    // reader is sitting at the end of a conversation.
    expect(isNearBottom(metrics(599.5, 1000))).toBe(true);
  });

  it("counts the threshold itself as pinned", () => {
    expect(isNearBottom(metrics(1000 - 400 - FOLLOW_THRESHOLD_PX, 1000))).toBe(true);
    expect(isNearBottom(metrics(1000 - 400 - FOLLOW_THRESHOLD_PX - 1, 1000))).toBe(false);
  });

  it("is pinned when there is nothing to scroll", () => {
    // A transcript shorter than the viewport already shows its newest message,
    // so it must not offer a "jump to latest" control for content in view.
    expect(isNearBottom(metrics(0, 300))).toBe(true);
    expect(isNearBottom(metrics(0, 400))).toBe(true);
  });

  it("defaults to pinned before the container has been measured", () => {
    // An unmeasured container reports zeros; treating that as "not at the
    // bottom" would stop a fresh conversation from following its own answer.
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 0, clientHeight: 0 })).toBe(true);
    expect(
      isNearBottom({ scrollTop: Number.NaN, scrollHeight: Number.NaN, clientHeight: Number.NaN }),
    ).toBe(true);
  });

  it("accepts a real scroll element's measurements", () => {
    // The component passes the element itself, so the parameter has to stay a
    // structural subset of what the DOM exposes.
    const element = { scrollTop: 10, scrollHeight: 1000, clientHeight: 400, id: "log" };
    expect(isNearBottom(element)).toBe(false);
  });
});
