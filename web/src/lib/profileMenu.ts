/**
 * The profile menu's model: which entries exist, in what order, and the copy for
 * the "delete the last 24 hours" flow.
 *
 * Pure and React-free on purpose. The test environment is `node` with no DOM
 * library, so `renderToString` never runs effects and a closed panel cannot be
 * opened, clicked or asserted — the rules that matter (which entries each
 * placement offers; an empty window must not offer a destructive confirm) only
 * become testable when they live here. Every placement's configuration is named
 * below for the same reason: a prop set spelled out at the call site is
 * invisible to a test, and that is how one of these rules previously ended up
 * living in a comment instead of an assertion.
 * `components/ProfileMenu.tsx` is then just rendering plus keyboard plumbing.
 */

import { SignOut, Trash } from "@phosphor-icons/react";
import type {
  DeleteHistoryPreviewResponse,
  DeleteHistoryRangeResponse,
  HistoryDeleteRange,
} from "@/api/types";
import { SUPPORT_LINKS } from "@/components/navItems";

export type ProfileMenuAction = "settings" | "billing" | "support" | "clear-history" | "logout";

export interface ProfileMenuItem {
  id: ProfileMenuAction;
  label: string;
  /** Router destination — present on links, absent on actions. */
  to?: string;
  icon: React.ElementType;
  /** Draw a rule above this entry to separate it from the group before it. */
  dividerBefore?: boolean;
  /** Renders in the destructive (rose) treatment. */
  destructive?: boolean;
}

export interface ProfileMenuOptions {
  /**
   * Whether the dropdown offers logout.
   *
   * True for all three current placements — the phone header and the public
   * header need it, and the desktop sidebar now does too (its dropdown is the
   * only way out once the old standalone sign-out icon was removed). It stays a
   * prop because "may this menu sign you out?" is a placement decision rather
   * than a property of the menu, and the desktop rail used to answer no: that
   * requirement was reversed, not deleted.
   */
  showLogout: boolean;
  /** The public header passes false: purging history is not a marketing-page action. */
  allowHistoryDelete: boolean;
  /**
   * Include the app destinations that the desktop sidebar's own rail already
   * lists (Billing, Support).
   *
   * Half of what this menu can show is ordinary navigation that happens to be
   * reachable from the avatar. In the sidebar that is pure duplication — the rail
   * sits inches to the left with the same two entries — so that placement turns
   * it off and the menu keeps only what is about the *account*: its settings, and
   * the way out. The phone's top bar and the public header have no rail, so they
   * keep them.
   */
  includeAppLinks: boolean;
}

/**
 * Link entries that are app destinations rather than account concerns.
 *
 * Exactly the entries `includeAppLinks` governs. Kept as an explicit list of
 * action ids (rather than a `kind` field on every item, which the renderer would
 * ignore) so the rule is one place to read; `profileMenu.test.ts` pins the
 * membership from both directions.
 */
const APP_LINK_ACTIONS: ReadonlySet<ProfileMenuAction> = new Set<ProfileMenuAction>([
  "billing",
  "support",
]);

/**
 * Each placement's configuration, named.
 *
 * These live here, rather than being spelled out across three components, so the
 * difference between the placements is one readable list — and so `profileMenu.test.ts`
 * can assert the panel each one actually produces. A closed menu renders nothing,
 * so a test of the *component* cannot see these entries; testing the named option
 * set is the only way the rule gets held down.
 */

/**
 * Desktop sidebar: the account's own concerns — its settings, the recent-history
 * purge, and the way out.
 *
 * `includeAppLinks: false` is what makes this different from the phone menu: the
 * rail is right beside it with the same destinations, so Billing and Support would
 * be a worse copy of the navigation rather than a menu. The purge belongs here for
 * the opposite reason — nothing in the rail destroys data, so it is genuinely only
 * reachable from this panel.
 */
export const SIDEBAR_MENU: ProfileMenuOptions = {
  showLogout: true,
  allowHistoryDelete: true,
  includeAppLinks: false,
};

/** Phone top bar: the only route to any of this at that width, so it carries everything. */
export const PHONE_MENU: ProfileMenuOptions = {
  showLogout: true,
  allowHistoryDelete: true,
  includeAppLinks: true,
};

/** Public header: an account shortcut on a marketing page — no app-destructive actions. */
export const PUBLIC_HEADER_MENU: ProfileMenuOptions = {
  showLogout: true,
  allowHistoryDelete: false,
  includeAppLinks: true,
};

/**
 * The action id of a link entry is its route's last segment (`/app/settings` →
 * `settings`), which is how `ProfileMenuAction` spells those three ids.
 *
 * The cast is checked where it can be: `SUPPORT_LINKS` is a fixed list and
 * `profileMenu.test.ts` pins all three ids, so a route that stops matching the
 * union fails there instead of silently producing an unrecognised action.
 */
function actionForRoute(to: string): ProfileMenuAction {
  return to.slice(to.lastIndexOf("/") + 1) as ProfileMenuAction;
}

/**
 * Labels the menu states differently from the navigation it mirrors.
 *
 * `SUPPORT_LINKS` supplies the labels so a rename in `navItems.ts` cannot leave
 * this menu behind — but "Settings" is the one entry that means something else
 * here. In the sidebar it sits in a list of app areas and is unambiguous; in a
 * menu opened from a person's own avatar it reads as "settings for the app" and
 * competes with "Billing" beside it, when what it actually opens is the account
 * settings page. One deliberate, named override beats an ambiguous label, and
 * keeping it in this map (rather than inline at the call site) keeps the
 * "derive from NAV" rule intact for everything else.
 */
const MENU_LABEL_OVERRIDES: Partial<Record<ProfileMenuAction, string>> = {
  settings: "Account settings",
};

/**
 * The menu's entries, in display order.
 *
 * Labels and icons come from `SUPPORT_LINKS` (which takes them from `NAV`), so
 * renaming "Settings" in the navigation model cannot leave the menu behind.
 */
export function profileMenuItems({
  showLogout,
  allowHistoryDelete,
  includeAppLinks,
}: ProfileMenuOptions): ProfileMenuItem[] {
  const items: ProfileMenuItem[] = SUPPORT_LINKS.filter(
    ({ to }) => includeAppLinks || !APP_LINK_ACTIONS.has(actionForRoute(to)),
  ).map(({ to, label, icon }) => {
    const id = actionForRoute(to);
    return { id, label: MENU_LABEL_OVERRIDES[id] ?? label, to, icon };
  });

  if (allowHistoryDelete) {
    items.push({
      id: "clear-history",
      label: "Delete last 24 hours",
      icon: Trash,
      dividerBefore: true,
      destructive: true,
    });
  }

  if (showLogout) {
    items.push({
      id: "logout",
      label: "Log out",
      icon: SignOut,
      dividerBefore: true,
      destructive: true,
    });
  }

  return items;
}

/** The window each delete range names, in the words the confirmation uses. */
const WINDOW_PHRASE: Record<HistoryDeleteRange, string> = {
  "24h": "the last 24 hours",
  "7d": "the last 7 days",
  "30d": "the last 30 days",
  all: "all time",
};

/** `"1 conversion"` / `"12 conversions"` — never `"1 conversions"`. */
function conversionCount(count: number): string {
  return `${count} ${count === 1 ? "conversion" : "conversions"}`;
}

function stillRunningClause(active: number): string {
  return active === 1
    ? `${conversionCount(active)} is still running and will be kept.`
    : `${conversionCount(active)} are still running and will be kept.`;
}

/**
 * The line the confirmation modal shows above its Delete button.
 *
 * It states the kept count as well as the doomed one: those are two different
 * numbers, and a user who agrees to "removes 12" and then sees 10 disappear has
 * been told something untrue. `count === 0` gets its own sentence because the
 * caller disables the confirm on it — "permanently removes 0 conversions" would
 * be a strange way to offer a destructive action.
 */
export function clearHistorySummary(preview: DeleteHistoryPreviewResponse): string {
  const window = WINDOW_PHRASE[preview.range] ?? WINDOW_PHRASE["24h"];

  if (preview.count === 0) {
    return `There are no conversions from ${window} to delete.`;
  }

  const base = `This permanently removes ${conversionCount(preview.count)} from ${window}.`;
  return preview.active_count > 0 ? `${base} ${stillRunningClause(preview.active_count)}` : base;
}

/**
 * The toast shown after a bulk delete.
 *
 * A zero result is not an error, so it is not reported as one — the window can
 * legitimately contain nothing but jobs that are still running, and those are
 * skipped rather than cancelled out from under a worker.
 */
export function clearedHistoryMessage(result: DeleteHistoryRangeResponse): string {
  const base =
    result.deleted_count === 0
      ? "Nothing to delete."
      : `${conversionCount(result.deleted_count)} deleted.`;

  if (result.skipped_active === 0) return base;

  const kept =
    result.skipped_active === 1
      ? `${conversionCount(result.skipped_active)} is still running and was kept.`
      : `${conversionCount(result.skipped_active)} are still running and were kept.`;
  return `${base} ${kept}`;
}
