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
  ClockCounterClockwise,
  CreditCard,
  FolderOpen,
  GearSix,
  Lifebuoy,
  List,
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
  { to: "/app/convert", label: "Convert", icon: ArrowsClockwise },
  { to: "/app/queue", label: "Queue", icon: List },
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
  { to: "/app/files", label: "Files", icon: FolderOpen },
  { to: "/app/billing", label: "Billing", icon: CreditCard },
  { to: "/app/settings", label: "Settings", icon: GearSix },
  { to: "/app/support", label: "Support", icon: Lifebuoy },
];

/** The four destinations that stay visible on a phone's bottom bar. */
export const PRIMARY = NAV.slice(0, 4);

/** The remaining destinations, which live behind the phone's "More" popover. */
export const MORE = NAV.slice(4);
