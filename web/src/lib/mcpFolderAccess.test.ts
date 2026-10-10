// Tests for the Files page indicator's pure helpers.
//
// The indicator backs a quiet decoration that must never take the page down, so
// the parts worth pinning are the ones a wrong answer would make visible: an
// unknown folder showing a badge, or a known one showing none.

import { describe, expect, it } from "vitest";
import { agentFolderLabel, buildAgentFolderMap } from "@/lib/mcpFolderAccess";
import type { McpFolderAccessEntry } from "@/api/types";

function entry(
  folder_id: string,
  client_name: string,
  grant_id = "g1",
): McpFolderAccessEntry {
  return { folder_id, client_name, grant_id };
}

describe("buildAgentFolderMap", () => {
  it("maps a folder to the agent that may reach it", () => {
    const map = buildAgentFolderMap([entry("f1", "Claude Desktop")]);
    expect(map.get("f1")).toBe("Claude Desktop");
  });

  it("returns nothing for an unknown folder", () => {
    const map = buildAgentFolderMap([entry("f1", "Claude Desktop")]);
    expect(map.get("f2")).toBeUndefined();
  });

  it("is empty for no entries", () => {
    expect(buildAgentFolderMap([]).size).toBe(0);
  });

  it("keeps the first agent when two reach the same folder, so it is stable", () => {
    const map = buildAgentFolderMap([entry("f1", "Claude Desktop"), entry("f1", "Other Agent")]);
    expect(map.get("f1")).toBe("Claude Desktop");
  });

  it("skips rows without a folder id or a name rather than showing a nameless badge", () => {
    const map = buildAgentFolderMap([entry("", "Claude Desktop"), entry("f2", "")]);
    expect(map.size).toBe(0);
  });
});

describe("agentFolderLabel", () => {
  it("names the application in a readable sentence", () => {
    expect(agentFolderLabel("Claude Desktop")).toBe("Used by Claude Desktop");
  });

  it("trims surrounding whitespace", () => {
    expect(agentFolderLabel("  Cursor  ")).toBe("Used by Cursor");
  });

  it("falls back to a generic name when the application is unnamed", () => {
    expect(agentFolderLabel("   ")).toBe("Used by an AI app");
  });
});
