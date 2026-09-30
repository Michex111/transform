// The geometry of a chat surface, as arithmetic rather than CSS.
//
// WHY THIS EXISTS AT ALL
// A chat column sized with `100dvh` (or `100vh`) is correct everywhere except
// the one platform that matters most here: iOS Safari does **not** shrink
// `dvh` when the on-screen keyboard opens. Picking an attachment re-focuses the
// composer's textarea, the keyboard slides up, the column keeps its old height,
// and the composer — pinned to the bottom of that column — ends up behind the
// keyboard. `interactive-widget=resizes-content` would fix it on Chromium and
// is ignored by iOS, so the only dependable source of truth is
// `window.visualViewport`, which *does* shrink.
//
// The measurement itself is DOM glue (a hook, in `useChatViewport.ts`). The
// arithmetic is here so it can be tested in the Node environment, where there
// is no layout engine to ask: how much room is left once the chrome above and
// below the column is subtracted, and what to do when there is almost none.

/**
 * The height a chat surface may occupy, in CSS pixels.
 *
 * `viewportHeight` is the *real* visible height (from `visualViewport`), which
 * shrinks with the iOS keyboard; `reservedPx` is everything that is not the
 * chat column — the header above it and the fixed navigation and padding below
 * it. `minPx` is an optional floor.
 *
 * Two deliberate choices:
 *   - Inputs are sanitised to `0`. A `NaN` or negative height would otherwise
 *     propagate straight into an inline `height` and collapse the column, and a
 *     nonsense viewport measurement is more likely mid-orientation than a zero.
 *   - The result is floored at `minPx` but never allowed to go below zero. On a
 *     viewport shorter than the chrome that surrounds the column the honest
 *     answer is "nothing is left", not a negative height.
 */
export function availableChatHeight({
  viewportHeight,
  reservedPx,
  minPx = 0,
}: {
  viewportHeight: number;
  reservedPx: number;
  minPx?: number;
}): number {
  const viewport = positiveOrZero(viewportHeight);
  const reserved = positiveOrZero(reservedPx);
  const floor = positiveOrZero(minPx);
  return Math.max(floor, Math.round(viewport - reserved));
}

/**
 * The height to measure against: the visual viewport when the browser reports
 * one, otherwise the window's own `innerHeight`.
 *
 * `visualViewport` is absent on older browsers and in the Node test
 * environment. Falling back to `innerHeight` keeps the layout working there; it
 * is the *iOS* case — where the two disagree while the keyboard is up — that
 * the visual viewport exists to solve.
 */
export function viewportHeightFrom(
  visualViewport: { height: number } | null | undefined,
  fallback: number,
): number {
  const measured = visualViewport?.height;
  if (typeof measured === "number" && Number.isFinite(measured) && measured > 0) {
    return measured;
  }
  return positiveOrZero(fallback);
}

/** A finite, non-negative number; anything else is `0`. */
function positiveOrZero(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0;
}
