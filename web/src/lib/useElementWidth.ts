import { useEffect, useRef, useState } from "react";

/**
 * Measure an element's rendered width, for a chart that must lay out in real
 * pixels rather than a stretched viewBox.
 *
 * Why this exists: the obvious responsive-SVG trick — a fixed `viewBox` with
 * `preserveAspectRatio="none"` — stretches the coordinate system, which
 * distorts every stroke width and glyph horizontally. A time-series chart with
 * elliptical axis labels and hairlines of varying thickness looks broken. So the
 * chart measures its container and computes geometry in real pixels, which is
 * also what makes the geometry itself unit-testable (it takes a width).

 * SSR-safe: with no `window` (the repo's tests render with `renderToString` in a
 * Node environment) it returns the fallback and never touches `ResizeObserver`.
 */
export function useElementWidth<T extends HTMLElement = HTMLDivElement>(
  fallback = 720,
): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;

    // Read once synchronously so the first paint after mount is already correct
    // (ResizeObserver fires asynchronously, which would otherwise flash the
    // fallback width for one frame).
    const initial = element.getBoundingClientRect().width;
    if (initial > 0) setWidth(initial);

    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width;
      if (typeof next === "number" && next > 0) setWidth(next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}
