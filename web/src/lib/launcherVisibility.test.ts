// Tests for the floating launcher's visibility rule.
//
// The launcher must be absent on the full-page assistant (the page already is
// the assistant) and below the desktop/tablet breakpoint (the panel has nowhere
// to sit). Both are pure inputs, so they are asserted here rather than through a
// rendered tree — the Vitest environment is Node with no DOM.

import { describe, expect, it } from "vitest";
import {
  ASSISTANT_ROUTE,
  isAssistantRoute,
  shouldShowLauncher,
} from "@/lib/launcherVisibility";

describe("isAssistantRoute", () => {
  it("matches the assistant route exactly", () => {
    expect(isAssistantRoute(ASSISTANT_ROUTE)).toBe(true);
  });

  it("tolerates a trailing slash", () => {
    expect(isAssistantRoute("/app/assistant/")).toBe(true);
  });

  it("does not match other app routes", () => {
    for (const pathname of ["/app/dashboard", "/app/files", "/app", "/", "/assistant"]) {
      expect(isAssistantRoute(pathname)).toBe(false);
    }
  });

  it("does not match a longer path that merely starts with the route", () => {
    expect(isAssistantRoute("/app/assistant-notes")).toBe(false);
    expect(isAssistantRoute("/app/assistant/123")).toBe(false);
  });

  it("treats a missing pathname as not-the-assistant", () => {
    expect(isAssistantRoute(null)).toBe(false);
    expect(isAssistantRoute(undefined)).toBe(false);
    expect(isAssistantRoute("")).toBe(false);
  });
});

describe("shouldShowLauncher", () => {
  it("shows on a wide viewport away from the assistant", () => {
    expect(shouldShowLauncher({ narrow: false, pathname: "/app/dashboard" })).toBe(true);
  });

  it("hides on the assistant route even on a wide viewport", () => {
    expect(shouldShowLauncher({ narrow: false, pathname: ASSISTANT_ROUTE })).toBe(false);
    expect(shouldShowLauncher({ narrow: false, pathname: "/app/assistant/" })).toBe(false);
  });

  it("hides on a narrow viewport even away from the assistant", () => {
    expect(shouldShowLauncher({ narrow: true, pathname: "/app/dashboard" })).toBe(false);
  });

  it("hides when both conditions hold", () => {
    expect(shouldShowLauncher({ narrow: true, pathname: ASSISTANT_ROUTE })).toBe(false);
  });
});
