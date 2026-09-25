// Tests for the scrollbar fade-away timing rules.
//
// The DOM half of `installScrollFade` cannot be tested here — this repo's
// vitest environment is `node` with no document — so the rules that actually
// decide when a scrollbar appears live in `createScrollFadeTracker`, which takes
// an injected scheduler. These cases pin the behaviour that is easy to get
// subtly wrong: the deadline must RESTART on every scroll event (a long flick
// must not blink the bar away mid-gesture), an active region must be reported
// once on entry and once on exit rather than on every event, regions must be
// tracked independently, and teardown must leave nothing pending.
//
// Real Vitest fake timers rather than a hand-rolled clock: the deadline is the
// thing under test, so the real `setTimeout` semantics should be what is
// exercised, including the boundary at exactly `idleMs`.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  SCROLL_FADE_ATTRIBUTE,
  SCROLL_FADE_IDLE_MS,
  createScrollFadeTracker,
  scrollFadeTarget,
} from "@/lib/scrollFade";

/**
 * A stand-in element.
 *
 * It carries `nodeType`, so it is a believable target for `scrollFadeTarget` as
 * well as a distinct identity for the tracker's per-element bookkeeping.
 */
function element(name: string): Element {
  return { nodeType: 1, name } as unknown as Element;
}

function nameOf(target: Element): string {
  return (target as unknown as { name: string }).name;
}

function createRecorder() {
  const calls: Array<{ target: Element; active: boolean }> = [];
  return {
    calls,
    setActive: (target: Element, active: boolean) => void calls.push({ target, active }),
    /**
     * The regions currently reported as active.
     *
     * Derived from each target's LAST report, not by filtering the log for
     * truthy entries — the log is an append-only history, so filtering it would
     * make any region that was ever activated look permanently active and every
     * fade-out assertion would pass or fail for the wrong reason.
     */
    activeTargets: () => {
      const latest = new Map<Element, boolean>();
      for (const call of calls) latest.set(call.target, call.active);
      return [...latest.entries()]
        .filter(([, active]) => active)
        .map(([target]) => nameOf(target));
    },
  };
}

/** A tracker wired to Vitest's fake timers. */
function createTracker(idleMs = SCROLL_FADE_IDLE_MS) {
  const recorder = createRecorder();
  const tracker = createScrollFadeTracker({
    idleMs,
    setActive: recorder.setActive,
    schedule: (callback, ms) => setTimeout(callback, ms) as unknown as number,
    cancel: (handle) => clearTimeout(handle),
  });
  return { recorder, tracker };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("scrollFadeTarget", () => {
  it("maps a document target to the root element", () => {
    // Scrolling the page itself reports `document`, not an element. Without this
    // mapping the page's own scrollbar would never be marked while every nested
    // region was.
    const root = { nodeType: 1, name: "html" };
    expect(scrollFadeTarget({ nodeType: 9, documentElement: root } as unknown as EventTarget)).toBe(
      root,
    );
  });

  it("passes an element through", () => {
    const el = element("strip");
    expect(scrollFadeTarget(el as unknown as EventTarget)).toBe(el);
  });

  it("ignores anything that is neither a document nor an element", () => {
    // A text node (3) and a `window`-ish target must not be coerced into marking
    // some unrelated node.
    expect(scrollFadeTarget(null)).toBeNull();
    expect(scrollFadeTarget({ nodeType: 3 } as unknown as EventTarget)).toBeNull();
    expect(scrollFadeTarget({} as EventTarget)).toBeNull();
  });

  it("survives a document with no root element", () => {
    expect(
      scrollFadeTarget({ nodeType: 9, documentElement: null } as unknown as EventTarget),
    ).toBeNull();
  });
});

describe("createScrollFadeTracker", () => {
  it("reports a region active on the first scroll and inactive after the idle window", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    tracker.mark(strip);
    expect(recorder.calls).toEqual([{ target: strip, active: true }]);

    // Still inside the window.
    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS - 1);
    expect(recorder.activeTargets()).toEqual(["strip"]);

    vi.advanceTimersByTime(1);
    expect(recorder.calls).toEqual([
      { target: strip, active: true },
      { target: strip, active: false },
    ]);
  });

  it("restarts the deadline on every event instead of expiring mid-gesture", () => {
    // The bug this prevents: a one-shot timer means a long or slow scroll fades
    // the bar away while the user is still scrolling.
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    tracker.mark(strip);
    for (let i = 0; i < 5; i++) {
      vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS - 100);
      tracker.mark(strip);
    }

    // Five near-idle intervals have passed, but the region never went inactive.
    expect(recorder.calls).toEqual([{ target: strip, active: true }]);

    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS);
    expect(recorder.calls).toEqual([
      { target: strip, active: true },
      { target: strip, active: false },
    ]);
  });

  it("does not re-report an already-active region on every scroll event", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    for (let i = 0; i < 20; i++) {
      tracker.mark(strip);
      vi.advanceTimersByTime(10);
    }

    // One report on the way in, and no fade-out has happened yet.
    expect(recorder.calls).toEqual([{ target: strip, active: true }]);
  });

  it("reports the region active again once it is scrolled after fading", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    tracker.mark(strip);
    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS);
    tracker.mark(strip);

    expect(recorder.calls).toEqual([
      { target: strip, active: true },
      { target: strip, active: false },
      { target: strip, active: true },
    ]);
  });

  it("does not let a superseded timer flip the region inactive early", () => {
    // Three marks must leave one live deadline, not three. If the earlier timers
    // survived they would fire mid-scroll and fade the bar out anyway.
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    tracker.mark(strip);
    vi.advanceTimersByTime(500);
    tracker.mark(strip);
    vi.advanceTimersByTime(500);
    tracker.mark(strip);

    // The first two deadlines (at 900 and 1400) have both passed, but the region
    // was re-marked at 1000, so it must still be up.
    expect(recorder.activeTargets()).toEqual(["strip"]);
    expect(recorder.calls).toHaveLength(1);

    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS);
    expect(recorder.activeTargets()).toEqual([]);
    expect(recorder.calls).toHaveLength(2);
  });

  it("tracks nested regions independently", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const outer = element("outer");
    const inner = element("inner");

    tracker.mark(outer);
    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS - 50);
    tracker.mark(inner);

    // Both are up at this point.
    expect(recorder.activeTargets()).toEqual(["outer", "inner"]);

    // The outer region's window closes on its own schedule; the inner one's
    // countdown is unaffected by it.
    vi.advanceTimersByTime(50);
    expect(recorder.activeTargets()).toEqual(["inner"]);

    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS - 50);
    expect(recorder.activeTargets()).toEqual([]);
  });

  it("clears every pending timer and active region on dispose", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    const strip = element("strip");

    tracker.mark(strip);
    tracker.dispose();

    expect(recorder.calls).toEqual([
      { target: strip, active: true },
      { target: strip, active: false },
    ]);

    // The cancelled deadline must not fire later and re-report a clean region.
    vi.advanceTimersByTime(SCROLL_FADE_IDLE_MS * 2);
    expect(recorder.calls).toHaveLength(2);
  });

  it("is safe to dispose without ever having marked anything", () => {
    vi.useFakeTimers();
    const { recorder, tracker } = createTracker();
    expect(() => tracker.dispose()).not.toThrow();
    expect(recorder.calls).toEqual([]);
  });

  it("uses the idle window it was given", () => {
    // The default is a product decision, so pin it rather than letting it drift
    // silently in an edit.
    expect(SCROLL_FADE_IDLE_MS).toBe(900);

    vi.useFakeTimers();
    const { recorder, tracker } = createTracker(50);
    const strip = element("strip");

    tracker.mark(strip);
    vi.advanceTimersByTime(49);
    expect(recorder.activeTargets()).toEqual(["strip"]);

    vi.advanceTimersByTime(1);
    expect(recorder.activeTargets()).toEqual([]);
  });
});

describe("SCROLL_FADE_ATTRIBUTE", () => {
  it("is the attribute the stylesheet reveals on", () => {
    // The stylesheet has no way to import this, so the literal is duplicated
    // there by necessity. Pinning it here at least makes a rename deliberate.
    expect(SCROLL_FADE_ATTRIBUTE).toBe("data-scrolling");
  });
});
