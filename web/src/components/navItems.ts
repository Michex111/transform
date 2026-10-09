/**
 * The application's navigation model.
 *
 * Lives outside `AppShell.tsx` because the file that renders the shell should
 * export only its component: mixing a data export into it breaks Fast Refresh
 * for the whole shell (a data edit would remount the tree instead of patching
 * it), which the `react-refresh/only-export-components` rule flags. Keeping the
 * model in its own module also makes the per-destination requests below
 * directly assertable — router state is not reflected in rendered HTML, so a
 * server render cannot verify them.
 */

import {
  ArrowsClockwise,
  ChartBar,
  ChartLine,
  ClockCounterClockwise,
  CreditCard,
  FolderOpen,
  GearSix,
  Lifebuoy,
  Lightning,
  List,
  Robot,
  Sparkle,
} from "@phosphor-icons/react";

import { withTimeline } from "@/lib/historyFilters";

export type NavItem = {
  to: string;
  label: string;
  icon: React.ElementType;
  /**
   * Optional request carried to the destination page with the navigation.
   *
   * Used instead of writing the destination's preferences ahead of time: the
   * intent travels with the click, so nothing changes if the user never arrives
   * (a middle-click into another tab, or navigating elsewhere), and the choice
   * of window stays the destination page's business.
   */
  state?: Record<string, unknown>;
};

/** Every destination in the shell, in display order. */
export const NAV: NavItem[] = [
  { to: "/app/dashboard", label: "Dashboard", icon: ChartBar },
  // Second, and therefore in the phone bar's primary four: the assistant is a
  // top-level way to work with a file, not a settings-page extra.
  { to: "/app/assistant", label: "Assistant", icon: Sparkle },
  { to: "/app/convert", label: "Convert", icon: ArrowsClockwise },
  { to: "/app/queue", label: "Queue", icon: List },
  // After Queue, and grouped with it: a saved workflow is a shortcut for work
  // you would otherwise repeat on the Convert page, so it belongs with the
  // conversion surfaces rather than in Account or Developer.
  { to: "/app/workflows", label: "Workflows", icon: Lightning },
  // Entering History from the navigation means "show me my history", so it asks
  // for the whole window rather than resuming whatever range the last visit
  // left persisted. Narrower windows are one tap away in the page's own
  // dropdown, and a link that wants one (the Dashboard's "View all" asks for
  // 7 days) passes its own state instead.
  {
    to: "/app/history",
    label: "History",
    icon: ClockCounterClockwise,
    state: withTimeline("all"),
  },
  { to: "/app/files", label: "Drive", icon: FolderOpen },
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/settings", label: "Settings", icon: GearSix },
  { to: "/app/support", label: "Support", icon: Lifebuoy },
  // Developer tooling sits last: it is what you reach for when something built
  // elsewhere is misbehaving, rather than a place you work from.
  { to: "/app/developer/api-logs", label: "API Logs", icon: ChartLine },
  { to: "/app/developer/mcp-activity", label: "MCP Activity", icon: Robot },
];

/** The four destinations that stay visible on a phone's bottom bar. */
export const PRIMARY = NAV.slice(0, 4);

/** The remaining destinations, which live behind the phone's "More" popover. */
export const MORE = NAV.slice(4);

/** Look one entry out of `NAV`, so a label or an icon is never written twice. */
function navItemFor(to: string): NavItem {
  const item = NAV.find((entry) => entry.to === to);
  if (!item) throw new Error(`No navigation entry for ${to}`);
  return item;
}

/**
 * The account destinations the profile menu links to, in menu order.
 *
 * Taken from `NAV` rather than re-typed: the menu and the sidebar show the same
 * three labels with the same three icons, and a hand-written copy is exactly how
 * they drift apart (rename "Settings" in `NAV` and the menu keeps the old word).
 *
 * The order here is the menu's order, which is not `NAV`'s: the sidebar groups
 * Billing with the other destinations, while the menu leads with Settings.
 */
export const SUPPORT_LINKS: NavItem[] = ["/app/settings", "/app/billing", "/app/support"].map(
  navItemFor,
);

/**
 * The one destination that leads a signed-in visitor from a public page back
 * into the app.
 *
 * Separate from `SUPPORT_LINKS` because it answers a different question: the
 * account destinations are offered by every placement, while the dashboard is
 * only worth an entry where the page itself has no navigation into the app (see
 * `showDashboard` in `lib/profileMenu.ts`). Looked up from `NAV` for the same
 * reason as `SUPPORT_LINKS` — the label and icon are written once and cannot
 * drift from the rail's.
 */
export const DASHBOARD_LINK: NavItem = navItemFor("/app/dashboard");

/** A labelled cluster of destinations in the desktop sidebar. */
export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * The desktop sidebar's grouping.
 *
 * The rail used to be one flat list of nine, which buried Convert and Drive at
 * the same weight as Support. The groups answer three different questions —
 * where you work with a document, what the AI can do, and what is about the
 * account — while `NAV` stays the single source of order for the phone bar
 * (`PRIMARY`/`MORE` are slices of it).
 *
 * Derived from `NAV` via `navItemFor`, so a destination cannot appear twice or
 * drift from the model the rest of the shell uses. A test pins that these
 * groups cover `NAV` exactly once, so no destination can become unreachable on
 * desktop only.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Workspace",
    items: [
      "/app/dashboard",
      "/app/convert",
      "/app/files",
      "/app/queue",
      "/app/workflows",
      "/app/history",
    ].map(navItemFor),
  },
  { label: "AI", items: ["/app/assistant"].map(navItemFor) },
  {
    label: "Account",
    items: ["/app/billing", "/app/settings", "/app/support"].map(navItemFor),
  },
  // The final section, as required: platform observability and agent access
  // control. Two destinations, kept separate on purpose — API request logs and
  // MCP tool activity answer different questions and are investigated by
  // different people.
  {
    label: "Developer",
    items: ["/app/developer/api-logs", "/app/developer/mcp-activity"].map(navItemFor),
  },
];
