// Tests for the conversation rail's grouping and relative timestamps.
//
// The clock is always injected and the instants are built from local Date
// components, so the cases behave the same in any timezone.

import { describe, expect, it } from "vitest";
import type { AssistantConversation } from "@/api/types";
import {
  filterConversations,
  groupConversations,
  relativeTimeLabel,
} from "@/lib/conversationGroups";

const NOW = new Date(2026, 8, 29, 12, 0, 0).getTime();

function conversation(
  id: string,
  iso: string,
  overrides: Partial<AssistantConversation> = {},
): AssistantConversation {
  return { id, title: `Chat ${id}`, created_at: iso, updated_at: iso, ...overrides };
}

function iso(year: number, month: number, day: number, hour = 9, minute = 0): string {
  return new Date(year, month, day, hour, minute, 0).toISOString();
}

describe("groupConversations", () => {
  it("buckets into Today / Previous 7 days / Earlier", () => {
    const groups = groupConversations(
      [
        conversation("today", iso(2026, 8, 29, 9)),
        conversation("yesterday", iso(2026, 8, 28, 22)),
        conversation("three-days", iso(2026, 8, 26, 9)),
        conversation("old", iso(2026, 7, 1, 9)),
      ],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual([
      "Today",
      "Previous 7 days",
      "Earlier",
    ]);
    expect(groups[1].items.map((c) => c.id)).toEqual(["yesterday", "three-days"]);
    expect(groups[2].items.map((c) => c.id)).toEqual(["old"]);
  });

  it("omits empty buckets rather than rendering empty headings", () => {
    const groups = groupConversations([conversation("old", iso(2026, 7, 1, 9))], NOW);
    expect(groups.map((group) => group.label)).toEqual(["Earlier"]);
  });

  it("sorts each bucket newest first", () => {
    const groups = groupConversations(
      [conversation("a", iso(2026, 8, 29, 8)), conversation("b", iso(2026, 8, 29, 11))],
      NOW,
    );
    expect(groups[0].items.map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("prefers updated_at over created_at when present", () => {
    const groups = groupConversations(
      [
        conversation("old-but-edited", iso(2026, 7, 1, 9), { updated_at: iso(2026, 8, 29, 10) }),
      ],
      NOW,
    );
    expect(groups[0].label).toBe("Today");
  });

  it("puts an unparseable timestamp in Earlier instead of crashing", () => {
    const groups = groupConversations(
      [conversation("bad", "not-a-date")],
      NOW,
    );
    expect(groups.map((group) => group.label)).toEqual(["Earlier"]);
  });

  it("returns nothing for an empty list", () => {
    expect(groupConversations([], NOW)).toEqual([]);
  });
});

describe("relativeTimeLabel", () => {
  it("reads as Just now under a minute, including slight clock skew", () => {
    expect(relativeTimeLabel(new Date(NOW - 30_000).toISOString(), NOW)).toBe("Just now");
    expect(relativeTimeLabel(new Date(NOW + 5_000).toISOString(), NOW)).toBe("Just now");
  });

  it("counts minutes and hours within the same day", () => {
    expect(relativeTimeLabel(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe("12m ago");
    expect(relativeTimeLabel(new Date(NOW - 3 * 3_600_000).toISOString(), NOW)).toBe("3h ago");
  });

  it("calls the previous calendar day Yesterday even if under 24h", () => {
    expect(relativeTimeLabel(iso(2026, 8, 28, 23, 0), NOW)).toBe("Yesterday");
  });

  it("counts days within the week", () => {
    expect(relativeTimeLabel(iso(2026, 8, 26, 9), NOW)).toBe("3d ago");
  });

  it("falls back to a date beyond a week", () => {
    const label = relativeTimeLabel(iso(2026, 7, 1, 9), NOW);
    expect(label).toMatch(/Aug|Jul/);
  });

  it("renders an empty string for absent or unparseable values", () => {
    expect(relativeTimeLabel(null, NOW)).toBe("");
    expect(relativeTimeLabel(undefined, NOW)).toBe("");
    expect(relativeTimeLabel("nope", NOW)).toBe("");
  });
});

describe("filterConversations", () => {
  const HISTORY = [
    conversation("a", iso(2026, 8, 29, 9), { title: "Convert my resume to PDF" }),
    conversation("b", iso(2026, 8, 29, 9), { title: "What files did I add recently?" }),
    conversation("c", iso(2026, 8, 29, 9), { title: "RESUME formatting advice" }),
  ];

  it("matches a title substring case-insensitively", () => {
    const found = filterConversations(HISTORY, "resume");
    expect(found.map((entry) => entry.id)).toEqual(["a", "c"]);
  });

  it("returns the list unchanged for a blank query", () => {
    // An empty box means "no filter", not "no results". Identity is asserted
    // rather than equality so a needless copy cannot creep in unnoticed.
    expect(filterConversations(HISTORY, "")).toBe(HISTORY);
    expect(filterConversations(HISTORY, "   ")).toBe(HISTORY);
  });

  it("ignores surrounding whitespace around the query", () => {
    expect(filterConversations(HISTORY, "  resume  ").map((entry) => entry.id)).toEqual(["a", "c"]);
  });

  it("returns nothing when no title matches", () => {
    expect(filterConversations(HISTORY, "zzzz")).toEqual([]);
  });

  it("tolerates a conversation with no title", () => {
    // The API can return an untitled conversation; it must not throw.
    const untitled = conversation("d", iso(2026, 8, 29, 9), { title: "" });
    expect(filterConversations([untitled], "anything")).toEqual([]);
  });
});
