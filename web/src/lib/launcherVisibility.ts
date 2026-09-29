// Whether the floating Transform AI launcher belongs on screen.
//
// Two independent reasons hide it, and both are "the control would do nothing
// useful here" cases:
//
//  1. A narrow viewport. The launcher is a desktop/tablet companion; on a phone
//     it would cover content and its panel has nowhere to sit. The component's
//     `hidden md:flex` classes and `useNarrowViewport()` are two halves of this
//     one decision and must agree (a control that exists only to toggle
//     something its own breakpoint hides does nothing when clicked).
//  2. The full-page assistant. On `/app/assistant` the whole page *is* the
//     assistant, so the launcher is redundant and its panel would duplicate the
//     transcript already on screen.
//
// Kept pure and out of the component so the rule is directly assertable: the
// test environment is Node with no DOM, and a route/breakpoint decision is
// exactly the part that must not depend on a rendered tree.

/** The route that renders the full-page assistant. */
export const ASSISTANT_ROUTE = "/app/assistant";

/**
 * Whether a pathname is the full-page assistant.
 *
 * Exact match, tolerating a single trailing slash (`/app/assistant/`) so a
 * hand-typed or redirected URL is still recognised. A longer path such as
 * `/app/assistant-notes` must NOT match, which is why this is not a prefix
 * check.
 */
export function isAssistantRoute(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  const normalised =
    pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return normalised === ASSISTANT_ROUTE;
}

/**
 * Whether the floating launcher should be rendered at all.
 *
 * `narrow` comes from {@link useNarrowViewport}; `pathname` from
 * `useLocation()`. Both must be clear for the button (and its panel) to exist.
 */
export function shouldShowLauncher({
  narrow,
  pathname,
}: {
  narrow: boolean;
  pathname: string | null | undefined;
}): boolean {
  return !narrow && !isAssistantRoute(pathname);
}
