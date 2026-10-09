/**
 * Tests for the navigation model, in particular the per-destination requests
 * it carries.
 *
 * These live apart from `AppShell.test.tsx` because router state never reaches
 * the DOM: a server render of the shell can show a link's href, but not the
 * `state` it will push. Asserting the model directly is the only way to pin
 * "the History entry asks for the full window" without a real browser.
 */

import { describe, expect, it } from "vitest";

import { MORE, NAV, NAV_GROUPS, PRIMARY, SUPPORT_LINKS } from "@/components/navItems";
import { HISTORY_TIMELINE_STATE_KEY, timelineFromNavigationState } from "@/lib/historyFilters";

describe("navigation model", () => {
  it("partitions every destination into the visible and overflow groups", () => {
    // The phone bar renders PRIMARY and the "More" popover renders MORE, so a
    // destination missing from both would be unreachable on a phone.
    expect([...PRIMARY, ...MORE]).toEqual(NAV);
    expect(PRIMARY).toHaveLength(4);
    expect(MORE).toHaveLength(NAV.length - 4);
  });

  it("lists each destination exactly once", () => {
    const paths = NAV.map((item) => item.to);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("gives every destination a label and an icon", () => {
    for (const item of NAV) {
      expect(item.label.length).toBeGreaterThan(0);
      expect(item.icon).toBeTruthy();
    }
  });
});

describe("History entry", () => {
  it("asks for the full window", () => {
    // Entering History from the navigation means "show me my history", so it
    // must not resume whatever range the last visit left persisted.
    const history = NAV.find((item) => item.to === "/app/history");
    expect(history).toBeDefined();
    expect(timelineFromNavigationState(history?.state)).toBe("all");
  });

  it("is reachable from the phone bar, not only the desktop sidebar", () => {
    // It sits in MORE now that the Assistant takes a primary slot, and the
    // bottom bar renders MORE inside its "More" popover — so it is still
    // reachable from a phone, which is the invariant this pins.
    expect(MORE.some((item) => item.to === "/app/history")).toBe(true);
  });

  it("carries the request under the key the page reads", () => {
    const history = NAV.find((item) => item.to === "/app/history");
    expect(history?.state).toHaveProperty(HISTORY_TIMELINE_STATE_KEY, "all");
  });
});

describe("Assistant entry", () => {
  it("sits directly after Dashboard, so it takes a primary slot", () => {
    // The assistant is a top-level way to work with a file, not a settings-page
    // extra: it is second in the model and therefore in the phone bar's four.
    expect(NAV[1].to).toBe("/app/assistant");
    expect(PRIMARY.some((item) => item.to === "/app/assistant")).toBe(true);
  });

  it("carries no router state", () => {
    expect(NAV.find((item) => item.to === "/app/assistant")?.state).toBeUndefined();
  });
});

describe("other destinations", () => {
  it("do not carry a request", () => {
    // Adding state to a page that does not read it is silently dead weight, and
    // would make this model an unreliable description of intent.
    const withState = NAV.filter((item) => item.state !== undefined).map((item) => item.to);
    expect(withState).toEqual(["/app/history"]);
  });
});

describe("sidebar groups", () => {
  it("cover every destination exactly once", () => {
    // The desktop sidebar renders NAV_GROUPS, not NAV. A destination present in
    // NAV but missing from the groups would be unreachable on desktop while the
    // phone bar still showed it — so this pins the two lists to each other.
    const grouped = NAV_GROUPS.flatMap((group) => group.items.map((item) => item.to));
    expect(grouped).toHaveLength(NAV.length);
    expect(new Set(grouped).size).toBe(NAV.length);
    expect([...grouped].sort()).toEqual(NAV.map((item) => item.to).sort());
  });

  it("give every group a label and at least one destination", () => {
    for (const group of NAV_GROUPS) {
      expect(group.label.length).toBeGreaterThan(0);
      expect(group.items.length).toBeGreaterThan(0);
    }
  });

  it("name the stored-files destination Drive, not Files", () => {
    // The route stays `/app/files` (renaming it would break existing links), but
    // the label must match the page, which calls itself "My Drive".
    expect(NAV.find((item) => item.to === "/app/files")?.label).toBe("Drive");
  });

  it("place Developer last, as its own section", () => {
    const last = NAV_GROUPS[NAV_GROUPS.length - 1];
    expect(last.label).toBe("Developer");
    // The two Developer pages are separate destinations, not one merged view:
    // API request logs and MCP agent activity answer different questions.
    expect(last.items.map((item) => item.to)).toEqual([
      "/app/developer/api-logs",
      "/app/developer/mcp-activity",
    ]);
  });

  it("reach the Developer pages from the phone bar's overflow", () => {
    // The desktop sidebar renders NAV_GROUPS, but a phone only renders
    // PRIMARY + MORE — so a Developer route absent from NAV would be
    // unreachable on a phone.
    for (const to of ["/app/developer/api-logs", "/app/developer/mcp-activity"]) {
      expect(MORE.some((item) => item.to === to)).toBe(true);
    }
  });
});

describe("profile menu links", () => {
  it("lists the account destinations in menu order", () => {
    // Menu order, not NAV order: the sidebar groups Billing with the other
    // destinations, the menu leads with Settings.
    expect(SUPPORT_LINKS.map((item) => item.to)).toEqual([
      "/app/settings",
      "/app/billing",
      "/app/support",
    ]);
  });

  it("reuses the navigation entries instead of re-typing them", () => {
    // Same object, not a copy: a copy is how the menu's "Settings" and the
    // sidebar's "Settings" end up being two different words.
    for (const link of SUPPORT_LINKS) {
      const nav = NAV.find((item) => item.to === link.to);
      expect(nav).toBeDefined();
      expect(link).toBe(nav);
    }
  });
});
