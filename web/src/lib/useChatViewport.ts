// The DOM half of the measured chat geometry (the arithmetic is in
// `chatViewport.ts`, where it is unit-tested in the Node environment).
//
// The chat columns used to be sized with `100dvh`, which iOS Safari refuses to
// shrink when the on-screen keyboard opens — so attaching a file re-focused the
// composer, the keyboard came up, and the composer was pushed underneath it.
// These hooks replace the viewport unit with a measurement of the *visible*
// viewport, which does shrink on iOS.

import { useCallback, useLayoutEffect, useState } from "react";
import { availableChatHeight, viewportHeightFrom } from "@/lib/chatViewport";

/** The visible height of the browser window, or `null` before hydration/SSR. */
function readViewportHeight(): number {
  return viewportHeightFrom(window.visualViewport, window.innerHeight);
}

/**
 * The visible viewport height, kept current as the keyboard opens and closes.
 *
 * `visualViewport` is the only reliable signal on iOS, where `resize` fires on
 * the visual viewport but `window.innerHeight` does not change. `scroll` is
 * subscribed to as well because Safari can report a settled height only after
 * the viewport has finished panning. `innerHeight`'s own `resize` is kept for
 * browsers without `visualViewport` (and for desktop window resizing).
 */
export function useVisualViewportHeight(): number | null {
  const [height, setHeight] = useState<number | null>(() =>
    typeof window === "undefined" ? null : readViewportHeight(),
  );

  useLayoutEffect(() => {
    if (typeof window === "undefined") return;
    const update = () => setHeight(readViewportHeight());
    // Read once more on mount: the initialiser may have run before the visual
    // viewport settled (or on a different window than the effect sees).
    update();
    const vv = window.visualViewport;
    vv?.addEventListener("resize", update);
    vv?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      vv?.removeEventListener("resize", update);
      vv?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);

  return height;
}

/** The bottom inset to reserve when the surface is anchored, not in flow. */
export interface ChatInsetOptions {
  /** A lower bound, applied only above the `sm` breakpoint by the caller. */
  minPx?: number;
  /**
   * Space below the surface, in pixels. Omit it for an in-flow column, where
   * the reserved space is measured from the page (`main`'s remaining height);
   * pass it for a fixed, bottom-anchored panel, where there is nothing to
   * measure.
   */
  bottomInsetPx?: number;
}

/**
 * Measure the height available to `ref`, re-measuring whenever the visible
 * viewport changes.
 *
 * The reserved space is computed live rather than hard-coded: the distance from
 * the top of the viewport to the element (which covers the shell's header and
 * padding) plus, for an in-flow column, whatever sits below `main` — the mobile
 * bottom navigation and the shell's own bottom padding. Measuring means the
 * three navigation shells that can surround this column do not each need a
 * magic number.
 *
 * Returns `null` until the first measurement, so a caller can render without an
 * inline height rather than with a guessed one.
 */
export function useChatColumnHeight(
  ref: { current: HTMLElement | null },
  { minPx = 0, bottomInsetPx }: ChatInsetOptions = {},
): number | null {
  const [height, setHeight] = useState<number | null>(null);

  const measure = useCallback(() => {
    if (typeof window === "undefined") return;
    const element = ref.current;
    if (!element) return;
    const vv = window.visualViewport;
    const viewportHeight = viewportHeightFrom(vv, window.innerHeight);
    // `getBoundingClientRect` is relative to the LAYOUT viewport, while the
    // visual viewport can be panned within it (`offsetTop`) — which is exactly
    // what iOS does to bring a focused field into view. Subtracting the pan puts
    // the measurement back in the coordinates the height actually lives in.
    // Clamped at zero so a column scrolled above the top cannot produce a
    // *negative* reserve and so a column taller than the screen.
    const top = Math.max(0, element.getBoundingClientRect().top - (vv?.offsetTop ?? 0));
    const bottom = bottomInsetPx ?? reservedBelowMain(element);
    setHeight(availableChatHeight({ viewportHeight, reservedPx: top + bottom, minPx }));
  }, [ref, minPx, bottomInsetPx]);

  useLayoutEffect(() => {
    if (typeof window === "undefined") return;
    // `useLayoutEffect` (not `useEffect`) so the first measurement lands before
    // the browser paints: otherwise the column would paint at its natural
    // height and then snap, which is the flash this whole change removes.
    measure();
    const vv = window.visualViewport;
    vv?.addEventListener("resize", measure);
    vv?.addEventListener("scroll", measure);
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    return () => {
      vv?.removeEventListener("resize", measure);
      vv?.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
    };
  }, [measure]);

  return height;
}

/**
 * The space reserved below the column by the shell: the fixed bottom navigation
 * (on phones) plus `main`'s own bottom padding.
 *
 * Measured against `innerHeight` rather than the visual viewport on purpose.
 * When the keyboard is up, the navigation is simply behind it; what must not
 * happen is that the reserved band *grows* as the keyboard covers the screen,
 * because that would shrink the column twice.
 */
function reservedBelowMain(element: HTMLElement): number {
  const main = element.closest("main");
  if (!main) return 0;
  const rect = main.getBoundingClientRect();
  const paddingBottom = Number.parseFloat(window.getComputedStyle(main).paddingBottom) || 0;
  return Math.max(0, window.innerHeight - rect.bottom) + paddingBottom;
}
