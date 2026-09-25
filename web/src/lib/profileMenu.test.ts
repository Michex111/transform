/**
 * The profile menu's rules: which entries exist, in which order, and the copy
 * for the 24-hour purge.
 *
 * These assertions are the ones that actually hold the requirements down. A
 * closed menu renders nothing, so `ProfileMenu.test.tsx` can only see the
 * markup once the panel is forced open — and a test of the *component* still
 * cannot see the props a particular placement passes. That is why each
 * placement's option set is named in the module and pinned here: the entry
 * presence rules (the sidebar shows the account's settings and logout but none
 * of the destinations its rail already lists; a marketing header offers no
 * purge; an empty window offers no destructive confirm) live on the model, where
 * they cannot hide behind a rendering detail or a call-site prop set.
 */

import { describe, expect, it } from "vitest";
import type { DeleteHistoryPreviewResponse, DeleteHistoryRangeResponse } from "@/api/types";
import {
  clearedHistoryMessage,
  clearHistorySummary,
  PHONE_MENU,
  profileMenuItems,
  PUBLIC_HEADER_MENU,
  SIDEBAR_MENU,
  type ProfileMenuOptions,
} from "@/lib/profileMenu";

const FULL: ProfileMenuOptions = {
  showLogout: true,
  allowHistoryDelete: true,
  includeAppLinks: true,
};

function ids(options: ProfileMenuOptions): string[] {
  return profileMenuItems(options).map((item) => item.id);
}

function preview(overrides: Partial<DeleteHistoryPreviewResponse> = {}): DeleteHistoryPreviewResponse {
  return { range: "24h", since: "2026-09-21T00:00:00Z", count: 12, active_count: 0, ...overrides };
}

function deleted(
  overrides: Partial<DeleteHistoryRangeResponse> = {},
): DeleteHistoryRangeResponse {
  return { deleted_count: 12, skipped_active: 0, range: "24h", ...overrides };
}

describe("profileMenuItems", () => {
  it("keeps a stable order: links first, then the destructive actions", () => {
    expect(ids(FULL)).toEqual(["settings", "billing", "support", "clear-history", "logout"]);
  });

  it("omits logout when the caller does not offer it", () => {
    // No placement passes false today — the phone header, the public header and
    // the desktop sidebar all offer logout. The option is still pinned because
    // it is a placement decision the component exposes, and the desktop rail
    // previously relied on the false branch (that requirement was reversed, not
    // forgotten, so a future placement can still turn it off).
    expect(ids({ showLogout: false, allowHistoryDelete: true, includeAppLinks: true })).not.toContain(
      "logout",
    );
  });

  it("renders exactly one logout entry, and it is last", () => {
    const items = profileMenuItems({ showLogout: true, allowHistoryDelete: true, includeAppLinks: true });
    const logouts = items.filter((item) => item.id === "logout");
    expect(logouts).toHaveLength(1);
    expect(items[items.length - 1]).toBe(logouts[0]);
  });

  it("drops the app destinations the sidebar rail already lists", () => {
    // The rule behind `includeAppLinks: false`. Billing and Support sit inches
    // away in the sidebar's own navigation, so repeating them in the account menu
    // makes it a worse copy of the rail beside it.
    expect(
      ids({ showLogout: true, allowHistoryDelete: true, includeAppLinks: false }),
    ).toEqual(["settings", "clear-history", "logout"]);
  });

  it("keeps the account entry when the app links are dropped", () => {
    // Not "hide every link" — opening your own account settings is the reason to
    // open this menu at all, even though the rail also lists it.
    expect(
      ids({ showLogout: true, allowHistoryDelete: false, includeAppLinks: false }),
    ).toEqual(["settings", "logout"]);
  });

  it("governs only the two app destinations, from both directions", () => {
    // Pins the membership of APP_LINK_ACTIONS: everything that disappears when
    // the option is off, and nothing else.
    const withLinks = ids({ showLogout: true, allowHistoryDelete: true, includeAppLinks: true });
    const withoutLinks = ids({ showLogout: true, allowHistoryDelete: true, includeAppLinks: false });
    expect(withLinks.filter((id) => !withoutLinks.includes(id))).toEqual(["billing", "support"]);
    // And the remaining entries keep their relative order.
    expect(withoutLinks).toEqual(withLinks.filter((id) => withoutLinks.includes(id)));
  });

  it("omits the history purge where it is not an app action", () => {
    expect(
      ids({ showLogout: true, allowHistoryDelete: false, includeAppLinks: true }),
    ).not.toContain("clear-history");
    expect(
      ids({ showLogout: false, allowHistoryDelete: false, includeAppLinks: true }),
    ).toEqual([
      "settings",
      "billing",
      "support",
    ]);
  });

  it("separates the destructive group from the links", () => {
    const items = profileMenuItems(FULL);
    const byId = (id: string) => items.find((item) => item.id === id);
    expect(byId("clear-history")?.dividerBefore).toBe(true);
    expect(byId("logout")?.dividerBefore).toBe(true);
    expect(byId("clear-history")?.destructive).toBe(true);
    expect(byId("logout")?.destructive).toBe(true);
    expect(byId("settings")?.destructive).toBeFalsy();
  });

  it("gives the links destinations and the actions none", () => {
    for (const item of profileMenuItems(FULL)) {
      if (item.id === "settings" || item.id === "billing" || item.id === "support") {
        expect(item.to).toMatch(/^\/app\//);
      } else {
        expect(item.to).toBeUndefined();
      }
      expect(item.label.length).toBeGreaterThan(0);
      expect(item.icon).toBeTruthy();
    }
  });

  it("labels the purge with the window it actually deletes", () => {
    const purge = profileMenuItems(FULL).find((item) => item.id === "clear-history");
    expect(purge?.label).toBe("Delete last 24 hours");
  });

  it("names the account entry unambiguously inside a personal menu", () => {
    // The destination is the account settings page. In the sidebar's list of app
    // areas "Settings" is clear; beside "Billing" under someone's own name it is
    // not, so this one label deliberately differs from the navigation it mirrors.
    const items = profileMenuItems(FULL);
    expect(items.find((item) => item.id === "settings")?.label).toBe("Account settings");
    // Everything else still tracks the navigation model.
    expect(items.find((item) => item.id === "billing")?.label).toBe("Billing");
    expect(items.find((item) => item.id === "support")?.label).toBe("Support");
  });

  it("keeps the override pointing at the settings route", () => {
    // Guards the pairing: a label that says "Account settings" must still open
    // the settings page, not merely read as if it would.
    const settings = profileMenuItems(FULL).find((item) => item.id === "settings");
    expect(settings?.to).toBe("/app/settings");
  });
});

/**
 * The three placements, pinned.
 *
 * A closed menu renders nothing, so `ProfileMenu.test.tsx` cannot see the entries
 * a specific placement offers — and neither could a test of `AppShell`. Naming
 * each option set and asserting the panel it produces is the only way these rules
 * are held down; before, the desktop's configuration lived in a comment.
 */
describe("placement configurations", () => {
  it("gives the desktop sidebar the account's own concerns: settings, the purge, logout", () => {
    // The rail is inches away with every destination already in it, so the menu
    // is not a second navigation — but nothing in the rail destroys data, so the
    // purge is only reachable from here and belongs here. Logout too: this panel
    // replaced the standalone sign-out icon that used to sit in the same row.
    expect(ids(SIDEBAR_MENU)).toEqual(["settings", "clear-history", "logout"]);
  });

  it("orders the desktop panel with the destructive actions after the link", () => {
    // Settings is a link; the purge and logout are actions, and the panel draws a
    // rule above each of them. Pinned because the visual grouping is the only
    // thing telling the user that two of the three are destructive.
    const items = profileMenuItems(SIDEBAR_MENU);
    expect(items.map((item) => item.dividerBefore ?? false)).toEqual([false, true, true]);
    expect(items.map((item) => item.destructive ?? false)).toEqual([false, true, true]);
    expect(items.map((item) => item.to)).toEqual(["/app/settings", undefined, undefined]);
  });

  it("gives the phone top bar everything, because it has no rail", () => {
    // At that width this menu is the only route to billing, support, the purge
    // and logout, so dropping the app links here would strand them.
    expect(ids(PHONE_MENU)).toEqual(["settings", "billing", "support", "clear-history", "logout"]);
  });

  it("gives the public header the links and logout, but no history purge", () => {
    // A marketing page should not offer to delete a signed-in visitor's history.
    expect(ids(PUBLIC_HEADER_MENU)).toEqual(["settings", "billing", "support", "logout"]);
  });

  it("keeps the sidebar rule from leaking into the wider placements", () => {
    // The specific regression this guards: turning the app links off globally
    // instead of only for the sidebar, which would leave the phone menu with just
    // its two account entries.
    for (const options of [PHONE_MENU, PUBLIC_HEADER_MENU]) {
      const appLinks = profileMenuItems(options).filter(
        (item) => item.id === "billing" || item.id === "support",
      );
      expect(appLinks).toHaveLength(2);
    }
  });
  it("offers the purge on the two placements that have no other route to it", () => {
    // Desktop and phone both purge; the public header does not (asserted above).
    // Stated as a loop so the rule is "every in-app placement", not a list that
    // someone has to remember to extend when a fourth placement appears.
    for (const options of [SIDEBAR_MENU, PHONE_MENU]) {
      expect(ids(options)).toContain("clear-history");
    }
  });});

describe("clearHistorySummary", () => {
  it("states the count it is about to remove", () => {
    expect(clearHistorySummary(preview({ count: 12 }))).toBe(
      "This permanently removes 12 conversions from the last 24 hours.",
    );
  });

  it("singularises a one-item window", () => {
    expect(clearHistorySummary(preview({ count: 1 }))).toBe(
      "This permanently removes 1 conversion from the last 24 hours.",
    );
  });

  it("says there is nothing to do rather than offering an empty delete", () => {
    // The caller disables the confirm button on this; "permanently removes 0
    // conversions" would be a strange thing to ask someone to agree to.
    expect(clearHistorySummary(preview({ count: 0 }))).toBe(
      "There are no conversions from the last 24 hours to delete.",
    );
  });

  it("admits how many running conversions are being kept", () => {
    expect(clearHistorySummary(preview({ count: 12, active_count: 2 }))).toBe(
      "This permanently removes 12 conversions from the last 24 hours. 2 conversions are still running and will be kept.",
    );
    expect(clearHistorySummary(preview({ count: 12, active_count: 1 }))).toBe(
      "This permanently removes 12 conversions from the last 24 hours. 1 conversion is still running and will be kept.",
    );
  });

  it("names the window the range actually covers", () => {
    expect(clearHistorySummary(preview({ range: "7d" }))).toContain("from the last 7 days");
    expect(clearHistorySummary(preview({ range: "30d" }))).toContain("from the last 30 days");
    expect(clearHistorySummary(preview({ range: "all", since: null }))).toContain("from all time");
  });
});

describe("clearedHistoryMessage", () => {
  it("reports what was removed", () => {
    expect(clearedHistoryMessage(deleted())).toBe("12 conversions deleted.");
    expect(clearedHistoryMessage(deleted({ deleted_count: 1 }))).toBe("1 conversion deleted.");
  });

  it("reports the jobs that were left alone", () => {
    expect(clearedHistoryMessage(deleted({ deleted_count: 12, skipped_active: 2 }))).toBe(
      "12 conversions deleted. 2 conversions are still running and were kept.",
    );
    expect(clearedHistoryMessage(deleted({ deleted_count: 1, skipped_active: 1 }))).toBe(
      "1 conversion deleted. 1 conversion is still running and was kept.",
    );
  });

  it("treats an empty window as nothing to do, not as a failure", () => {
    expect(clearedHistoryMessage(deleted({ deleted_count: 0 }))).toBe("Nothing to delete.");
    // And it still explains itself when the window was non-empty but everything
    // in it is still running.
    expect(clearedHistoryMessage(deleted({ deleted_count: 0, skipped_active: 3 }))).toBe(
      "Nothing to delete. 3 conversions are still running and were kept.",
    );
  });
});
