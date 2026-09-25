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

import { MORE, NAV, PRIMARY, SUPPORT_LINKS } from "@/components/navItems";
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
    // It sits in PRIMARY, which both the sidebar and the bottom bar render —
    // so the request has to be on the model, not attached to one render site.
    expect(PRIMARY.some((item) => item.to === "/app/history")).toBe(true);
  });

  it("carries the request under the key the page reads", () => {
    const history = NAV.find((item) => item.to === "/app/history");
    expect(history?.state).toHaveProperty(HISTORY_TIMELINE_STATE_KEY, "all");
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
