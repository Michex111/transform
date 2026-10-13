import { NAV } from "@/components/navItems";

/**
 * The document title for a route.
 *
 * WHY this is a separate module
 * -----------------------------
 * The SPA ships one `index.html`, so without this the browser tab, the window
 * list and every history entry read the marketing title — "Transform — Convert,
 * store, and automate your documents" — no matter which page you are on. That is
 * actively unhelpful for the two places a title is most read: a browser tab among
 * many, and the back/forward history list. `SeoHead` already solves this for the
 * public pages; this covers the signed-in shell.
 *
 * The labels come from the navigation model rather than a second list, so a
 * renamed destination cannot end up with a stale title.
 *
 * Detail routes are named by what they are, not by the id in the URL: a tab
 * reading "d3f1a…" tells the reader nothing. Where a page knows a better name
 * (a workspace's own name) it sets the title itself after loading, and wins —
 * this only runs when the path changes.
 */
export const APP_NAME = "Transform";

/** Longest-prefix matches, applied when no exact navigation entry matches. */
const DETAIL_TITLES: readonly [prefix: string, title: string][] = [
  ["/app/workspaces/", "Workspace"],
  ["/app/settings/", "Settings"],
  ["/app/billing/", "Billing"],
  ["/app/developer/", "Developer"],
];

export function titleForPath(pathname: string): string {
  // `/app/dashboard` and `/app/dashboard/` are the same page; normalise the
  // trailing slash so a link with one is not the untitled case.
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;

  const exact = NAV.find((item) => item.to === path);
  if (exact) return `${exact.label} · ${APP_NAME}`;

  for (const [prefix, title] of DETAIL_TITLES) {
    if (path.startsWith(prefix)) return `${title} · ${APP_NAME}`;
  }

  if (path === "/app" || path === "/app/") return `${APP_NAME}`;
  // An unknown signed-in route is still the app rather than the marketing site:
  // saying so beats inheriting a title that describes a page the user left.
  return `${APP_NAME}`;
}
