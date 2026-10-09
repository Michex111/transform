// Whether a scrolling transcript should keep following its newest content.
//
// Kept pure and DOM-free so the rule can be tested without a layout engine: the
// bug it prevents is invisible in a screenshot — the viewport is pinned to the
// bottom either way — and only shows up as the transcript yanking itself down
// while someone is reading an earlier answer.

/** The three measurements every scroll container exposes. */
export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/**
 * How close to the bottom still counts as "at the bottom", in pixels.
 *
 * Not zero: sub-pixel layout and the last message's own margin mean an exactly
 * aligned bottom is rare, and a threshold of zero would unpin the transcript
 * during ordinary reading at the end of a conversation.
 */
export const FOLLOW_THRESHOLD_PX = 64;

/**
 * True when the container is close enough to its end that new content should
 * scroll into view.
 *
 * A container with nothing to scroll (content shorter than the viewport) is
 * always "at the bottom", so a short transcript stays pinned rather than
 * offering a "jump to latest" control for content that already fits.
 */
export function isNearBottom(
  metrics: ScrollMetrics,
  threshold: number = FOLLOW_THRESHOLD_PX,
): boolean {
  const { scrollTop, scrollHeight, clientHeight } = metrics;
  if (!Number.isFinite(scrollTop) || !Number.isFinite(scrollHeight) || !Number.isFinite(clientHeight)) {
    // A container that has not been measured yet must not block the follow
    // behaviour; treating it as pinned is the safer default because it matches
    // the initial render, where the newest message is what the user wants.
    return true;
  }
  if (scrollHeight <= clientHeight) return true;
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
