/**
 * Tests for the history/queue filter values shared between pages.
 *
 * Kept as pure-logic tests (no rendering) because the value that matters most
 * here — the mapping from a UI filter to the API's `range` parameter — is what
 * broke: the timeline dropdown persisted its selection but stopped querying with
 * it, so picking "Last 7 days" filtered nothing.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  HISTORY_TIMELINE_STATE_KEY,
  VALID_STATUSES,
  VALID_TIMELINES,
  isStatusFilter,
  isTimelineFilter,
  readStoredStatus,
  readStoredTimeline,
  refreshRangeFor,
  timelineForNavigation,
  timelineFromNavigationState,
  withTimeline,
  writeStoredStatus,
  writeStoredTimeline,
} from "@/lib/historyFilters";

/** Minimal localStorage stand-in; the Vitest environment is `node`. */
class MemoryStorage {
  private readonly map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
  clear(): void {
    this.map.clear();
  }
}

const realLocalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const realWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  // Restore both descriptors so a stubbed storage cannot leak into another suite.
  if (realLocalStorage) Object.defineProperty(globalThis, "localStorage", realLocalStorage);
  else Reflect.deleteProperty(globalThis, "localStorage");
  if (realWindow) Object.defineProperty(globalThis, "window", realWindow);
  else Reflect.deleteProperty(globalThis, "window");
});

describe("refreshRangeFor", () => {
  it("omits the parameter for 'all'", () => {
    // The API treats an unknown range as "no lower bound", so sending the
    // literal "all" would work by accident rather than by contract.
    expect(refreshRangeFor("all")).toBeUndefined();
  });

  it("forwards every real window", () => {
    expect(refreshRangeFor("24h")).toBe("24h");
    expect(refreshRangeFor("7d")).toBe("7d");
    expect(refreshRangeFor("30d")).toBe("30d");
  });

  it("returns a value the API documents for every non-'all' filter", () => {
    // Guards against a new dropdown option that has no server-side mapping.
    const sent = VALID_TIMELINES.filter((t) => t !== "all").map((t) => refreshRangeFor(t));
    expect(sent.every((v) => typeof v === "string")).toBe(true);
    expect(new Set(VALID_TIMELINES.filter((t) => t !== "all"))).toEqual(new Set(sent));
  });
});

describe("timelineFromNavigationState", () => {
  it("reads the window a link asked for", () => {
    expect(timelineFromNavigationState(withTimeline("7d"))).toBe("7d");
  });

  it("round-trips every valid filter", () => {
    for (const timeline of VALID_TIMELINES) {
      expect(timelineFromNavigationState(withTimeline(timeline))).toBe(timeline);
    }
  });

  it("returns null for absent or malformed state", () => {
    // A hand-typed URL, a stale history.state, or another feature's state must
    // never put an invalid value in front of the API.
    expect(timelineFromNavigationState(undefined)).toBeNull();
    expect(timelineFromNavigationState(null)).toBeNull();
    expect(timelineFromNavigationState("7d")).toBeNull();
    expect(timelineFromNavigationState(7)).toBeNull();
    expect(timelineFromNavigationState({})).toBeNull();
    expect(timelineFromNavigationState({ [HISTORY_TIMELINE_STATE_KEY]: "90d" })).toBeNull();
    expect(timelineFromNavigationState({ [HISTORY_TIMELINE_STATE_KEY]: 7 })).toBeNull();
    expect(timelineFromNavigationState({ [HISTORY_TIMELINE_STATE_KEY]: null })).toBeNull();
  });

  it("ignores unrelated navigation state", () => {
    expect(timelineFromNavigationState({ from: "/app/dashboard" })).toBeNull();
  });
});

describe("timelineForNavigation", () => {
  it("adopts the window a navigation asks for", () => {
    expect(timelineForNavigation(withTimeline("7d"), "all")).toBe("7d");
  });

  it("resets to all when the navigation asks for the full window", () => {
    // This is the sidebar/phone-bar History click: it must override whatever
    // the previous visit left selected, not just apply on a fresh mount.
    expect(timelineForNavigation(withTimeline("all"), "30d")).toBe("all");
  });

  it("keeps the current window when the navigation asks for nothing", () => {
    // An unrelated navigation, or a re-render the router did not cause, must
    // not silently change what the user is looking at.
    expect(timelineForNavigation(undefined, "30d")).toBe("30d");
    expect(timelineForNavigation(null, "24h")).toBe("24h");
    expect(timelineForNavigation({}, "7d")).toBe("7d");
    expect(timelineForNavigation({ from: "/app/dashboard" }, "7d")).toBe("7d");
  });

  it("keeps the current window when the navigation asks for something invalid", () => {
    expect(timelineForNavigation({ timeline: "90d" }, "7d")).toBe("7d");
    expect(timelineForNavigation({ timeline: 7 }, "7d")).toBe("7d");
  });

  it("applies every valid request onto any previous window", () => {
    for (const requested of VALID_TIMELINES) {
      for (const previous of VALID_TIMELINES) {
        expect(timelineForNavigation(withTimeline(requested), previous)).toBe(requested);
      }
    }
  });
});

describe("stored preferences", () => {
  it("round-trips the timeline", () => {
    writeStoredTimeline("30d");
    expect(readStoredTimeline()).toBe("30d");
  });

  it("round-trips the status", () => {
    writeStoredStatus("FAILED");
    expect(readStoredStatus()).toBe("FAILED");
  });

  it("defaults to 'all' when nothing is stored", () => {
    expect(readStoredTimeline()).toBe("all");
    expect(readStoredStatus()).toBe("all");
  });

  it("rejects an unrecognised stored value", () => {
    localStorage.setItem("historyPageTimeline", "90d");
    localStorage.setItem("historyPageStatus", "EXPLODED");
    expect(readStoredTimeline()).toBe("all");
    expect(readStoredStatus()).toBe("all");
  });

  it("survives storage that throws (private mode, sandboxed iframe)", () => {
    Object.defineProperty(globalThis, "localStorage", {
      value: {
        getItem() {
          throw new Error("storage blocked");
        },
        setItem() {
          throw new Error("storage blocked");
        },
      },
      configurable: true,
      writable: true,
    });

    expect(() => writeStoredTimeline("7d")).not.toThrow();
    expect(() => writeStoredStatus("COMPLETED")).not.toThrow();
    expect(readStoredTimeline()).toBe("all");
    expect(readStoredStatus()).toBe("all");
  });
});

describe("validators", () => {
  it("accept only the documented values", () => {
    for (const value of VALID_TIMELINES) expect(isTimelineFilter(value)).toBe(true);
    for (const value of VALID_STATUSES) expect(isStatusFilter(value)).toBe(true);

    for (const value of ["", "90d", "7D", "all ", 7, null, undefined, {}, []]) {
      expect(isTimelineFilter(value)).toBe(false);
      expect(isStatusFilter(value)).toBe(false);
    }
  });
});
