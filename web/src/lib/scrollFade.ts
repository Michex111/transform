/**
 * Scrollbar fade-away.
 *
 * The stylesheet keeps a scroll region's scrollbar invisible at rest and brings
 * it back while the region is in use (see the scrollbar block in `index.css`).
 * Revealing on `:hover`/`:focus-within` is enough for nested regions, but it
 * cannot work for the page's own scrollbar: `html:hover` is true whenever the
 * pointer is anywhere over the page, so the page bar would simply never fade.
 * That is what this module adds — a "this region is being scrolled right now"
 * signal, marked as an attribute on the scrolled element and cleared shortly
 * after the last scroll event.
 *
 * One capture-phase listener covers everything. Scroll events do not bubble, so
 * a bubble-phase listener on the document would only ever see the document's own
 * scrolling; in the capture phase the same listener also sees every descendant,
 * which is why this needs no per-component wiring and no React involvement.
 *
 * The timer bookkeeping is in `createScrollFadeTracker` — pure, injected
 * scheduler, no DOM — because this repo's test environment is `node` with no
 * document, so anything left inside `installScrollFade` could not be tested.
 */

/** The attribute the stylesheet keys off. Kept here so the two cannot drift. */
export const SCROLL_FADE_ATTRIBUTE = "data-scrolling";

/**
 * How long a region stays revealed after its last scroll event.
 *
 * Long enough that a flick of the wheel does not blink the bar away before the
 * eye lands on it, short enough that it is gone by the time you are reading.
 */
export const SCROLL_FADE_IDLE_MS = 900;

/** Node type constants, spelled out so this module needs no DOM globals. */
const DOCUMENT_NODE = 9;
const ELEMENT_NODE = 1;

/**
 * The element whose scrollbar a scroll event belongs to.
 *
 * A scroll event from the **document** — the page itself scrolling — reports
 * `document` as its target rather than an element, so this has to map it to
 * `documentElement` or the page's own scrollbar would never be marked while
 * every nested region was. Anything that is not an element (a removed node, a
 * `window` target) is ignored rather than coerced, so a stray event cannot mark
 * the wrong node or throw.
 */
export function scrollFadeTarget(target: EventTarget | null): Element | null {
  if (!target) return null;
  const nodeType = (target as { nodeType?: number }).nodeType;
  if (nodeType === DOCUMENT_NODE) return (target as Document).documentElement ?? null;
  if (nodeType === ELEMENT_NODE) return target as Element;
  return null;
}

export interface ScrollFadeTrackerOptions {
  /** Overridable for tests; defaults to {@link SCROLL_FADE_IDLE_MS}. */
  idleMs?: number;
  /** Report a region entering or leaving the "in use" state. */
  setActive: (target: Element, isActive: boolean) => void;
  /** Injected so the timing rules can be tested without a browser. */
  schedule: (callback: () => void, ms: number) => number;
  cancel: (handle: number) => void;
}

export interface ScrollFadeTracker {
  /** Note that `target` was just scrolled. */
  mark: (target: Element) => void;
  /** Cancel pending timers and return every active region to its resting state. */
  dispose: () => void;
}

/**
 * Track which regions are currently being scrolled.
 *
 * Marking is **restarting a deadline, not starting a one-shot timer**: every
 * event while a scroll is in progress resets that region's countdown, so a long
 * flick keeps the bar up for its whole duration and the fade-out always begins
 * from the last movement. Marking an already-active region therefore cancels its
 * pending timer, while the region itself is reported only once on the way in and
 * once on the way out — the attribute write is idempotent, but notifying on every
 * scroll event would be wasteful for no gain.
 *
 * Regions are tracked per element, so scrolling two nested containers does not
 * make either one's countdown depend on the other's.
 */
export function createScrollFadeTracker({
  idleMs = SCROLL_FADE_IDLE_MS,
  setActive,
  schedule,
  cancel,
}: ScrollFadeTrackerOptions): ScrollFadeTracker {
  const timers = new Map<Element, number>();
  const active = new Set<Element>();

  function expire(target: Element) {
    timers.delete(target);
    active.delete(target);
    setActive(target, false);
  }

  function mark(target: Element) {
    if (!active.has(target)) {
      active.add(target);
      setActive(target, true);
    }
    const pending = timers.get(target);
    if (pending !== undefined) cancel(pending);
    timers.set(
      target,
      schedule(() => expire(target), idleMs),
    );
  }

  function dispose() {
    for (const handle of timers.values()) cancel(handle);
    timers.clear();
    for (const target of active) setActive(target, false);
    active.clear();
  }

  return { mark, dispose };
}

/** The live teardown, so a repeated `install` cannot stack listeners. */
let installedTeardown: (() => void) | null = null;

/**
 * Start revealing scrollbars while their region is scrolled.
 *
 * Idempotent: a hot reload of the entry module (or a double-invoked effect) gets
 * the existing teardown instead of a second document listener.
 */
export function installScrollFade(): () => void {
  if (installedTeardown) return installedTeardown;
  if (typeof document === "undefined") return () => {};

  const tracker = createScrollFadeTracker({
    setActive(target, isActive) {
      if (isActive) target.setAttribute(SCROLL_FADE_ATTRIBUTE, "");
      else target.removeAttribute(SCROLL_FADE_ATTRIBUTE);
    },
    schedule: (callback, ms) => window.setTimeout(callback, ms),
    cancel: (handle) => window.clearTimeout(handle),
  });

  const onScroll = (event: Event) => {
    const target = scrollFadeTarget(event.target);
    if (target) tracker.mark(target);
  };

  document.addEventListener("scroll", onScroll, { capture: true, passive: true });

  installedTeardown = () => {
    document.removeEventListener("scroll", onScroll, { capture: true });
    tracker.dispose();
    installedTeardown = null;
  };
  return installedTeardown;
}
