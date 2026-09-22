/**
 * History/Queue filter values and their persistence.
 *
 * These lived inside `HistoryPage`, which meant the Dashboard had to import
 * from a *page* module just to hand History a filter. Anything shared between
 * pages belongs here instead: a page module pulls in the whole page (and its
 * component tree) and turns a one-line cross-page hand-off into a page→page
 * dependency.
 *
 * Everything here is pure or localStorage-only, so it is testable without
 * rendering — which matters because the test environment is `node` with no
 * testing library, and `renderToString` does not run effects.
 */

/** The only filter values the status dropdown can take. */
export const VALID_STATUSES = ["all", "COMPLETED", "PROCESSING", "PENDING", "FAILED"] as const;
export type StatusFilter = (typeof VALID_STATUSES)[number];

/** The only filter values the timeline dropdown can take. */
export const VALID_TIMELINES = ["all", "24h", "7d", "30d"] as const;
export type TimelineFilter = (typeof VALID_TIMELINES)[number];

/** The timeline values the API range parameter accepts (`all` is local-only). */
export type TimelineRange = Exclude<TimelineFilter, "all">;

const STATUS_KEY = "historyPageStatus";
const TIMELINE_KEY = "historyPageTimeline";

/**
 * Router-state key used to hand History a starting window.
 *
 * Exported so a link and the page cannot disagree about the key name.
 */
export const HISTORY_TIMELINE_STATE_KEY = "timeline";

export function isTimelineFilter(value: unknown): value is TimelineFilter {
  return typeof value === "string" && (VALID_TIMELINES as readonly string[]).includes(value);
}

export function isStatusFilter(value: unknown): value is StatusFilter {
  return typeof value === "string" && (VALID_STATUSES as readonly string[]).includes(value);
}

/**
 * Read a filter from localStorage, tolerating environments where storage is
 * blocked or unavailable (private mode, sandboxed preview iframes, disabled
 * cookies). Any unrecognised stored value falls back to `fallback` rather than
 * reaching the UI.
 */
function readStored<T extends string>(key: string, valid: readonly T[], fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return (valid as readonly string[]).includes(raw ?? "") ? (raw as T) : fallback;
  } catch {
    return fallback;
  }
}

/** Persist a filter, ignoring storage failures so blocked storage never breaks the page. */
function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — persistence is best-effort */
  }
}

export function readStoredStatus(): StatusFilter {
  return readStored(STATUS_KEY, VALID_STATUSES, "all");
}

export function writeStoredStatus(status: StatusFilter): void {
  writeStored(STATUS_KEY, status);
}

export function readStoredTimeline(): TimelineFilter {
  return readStored(TIMELINE_KEY, VALID_TIMELINES, "all");
}

export function writeStoredTimeline(timeline: TimelineFilter): void {
  writeStored(TIMELINE_KEY, timeline);
}

/**
 * The `range` query value for a timeline filter.
 *
 * `all` must send **no** parameter at all rather than the literal string
 * `"all"`: the API maps unknown values to "no lower bound", so sending `"all"`
 * happens to work today but only by falling through the same branch that
 * handles a typo. Omitting it keeps "everything" an explicitly supported case.
 */
export function refreshRangeFor(timeline: TimelineFilter): TimelineRange | undefined {
  return timeline === "all" ? undefined : timeline;
}

/**
 * Read a starting window out of router navigation state.
 *
 * Returns `null` when absent or malformed, so the caller can fall back to the
 * stored preference. Validating here means a hand-typed URL or a stale
 * `history.state` cannot put an invalid value in front of the API.
 */
export function timelineFromNavigationState(state: unknown): TimelineFilter | null {
  if (!state || typeof state !== "object") return null;
  const candidate = (state as Record<string, unknown>)[HISTORY_TIMELINE_STATE_KEY];
  return isTimelineFilter(candidate) ? candidate : null;
}

/**
 * The window a navigation should produce, given the one currently shown.
 *
 * A navigation carrying a request always wins (that is what makes the
 * navigation's History entry reset the window); one carrying none leaves the
 * current selection alone, so an unrelated navigation — or a re-render that the
 * router did not cause — cannot silently change what the user is looking at.
 *
 * Kept as a pure function so this decision is testable: the effect that calls it
 * cannot be exercised here, because the test environment is `node` and a server
 * render does not run effects.
 */
export function timelineForNavigation(
  state: unknown,
  previous: TimelineFilter,
): TimelineFilter {
  return timelineFromNavigationState(state) ?? previous;
}

/**
 * Navigation state asking History to open on `timeline`.
 *
 * Expressed as a link property rather than a localStorage write: the intent
 * travels with the navigation, costs nothing if the user never arrives, and
 * cannot leak into a later visit — whereas writing storage first mutates the
 * user's saved preference as a side effect of merely clicking a link.
 */
export function withTimeline(timeline: TimelineFilter): Record<string, TimelineFilter> {
  return { [HISTORY_TIMELINE_STATE_KEY]: timeline };
}
