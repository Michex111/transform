import { useEffect, useState } from "react";

/**
 * Subscribe to a CSS media query.
 *
 * Two deliberate choices:
 *
 *  - The state is initialised **synchronously** from `matchMedia` rather than
 *    from an effect. `useState(false)` plus a mount effect paints one frame of
 *    the desktop layout on a phone and then swaps it, which reads as a flash
 *    and, when the two layouts have different heights, as a layout shift.
 *  - The initialiser (and the effect) tolerate a missing `matchMedia`/
 *    `window`, so this renders `false` under `renderToString` — the test
 *    environment is Node with no DOM at all, and `matchMedia` is also absent
 *    from jsdom, so the guard is not hypothetical.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return false;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    // Re-read on mount: `query` may have changed since the initialiser ran.
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** Everything below Tailwind's `md` breakpoint (768px). */
export const NARROW_VIEWPORT_QUERY = "(max-width: 767px)";

/**
 * True on the narrow band where the data tables become expandable cards.
 *
 * Kept in lockstep with the `md:` classes on the rows themselves: the rows are
 * laid out by CSS, but the disclosure is a *structural* difference (a
 * `<button>` instead of a bare `<span>`, and a detail panel that does not exist
 * otherwise), and a control that exists only to toggle something its own
 * breakpoint hides is a control that does nothing when clicked.
 */
export function useNarrowViewport(): boolean {
  return useMediaQuery(NARROW_VIEWPORT_QUERY);
}
