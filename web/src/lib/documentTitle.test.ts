import { describe, expect, it } from "vitest";
import { APP_NAME, titleForPath } from "@/lib/documentTitle";
import { NAV } from "@/components/navItems";

describe("titleForPath", () => {
  it("names every navigation destination after its own label", () => {
    // Derived from the navigation model, so this also fails if a destination is
    // added without a usable label.
    for (const item of NAV) {
      expect(titleForPath(item.to)).toBe(`${item.label} · ${APP_NAME}`);
    }
  });

  it("ignores a trailing slash", () => {
    expect(titleForPath("/app/dashboard/")).toBe(titleForPath("/app/dashboard"));
  });

  it("names a detail route by what it is, not by its id", () => {
    const title = titleForPath("/app/workspaces/d3f1a4b2-0000-4000-8000-000000000000");
    expect(title).toBe(`Workspace · ${APP_NAME}`);
    expect(title).not.toContain("d3f1a4b2");
  });

  it("falls back to the app name rather than a stale marketing title", () => {
    expect(titleForPath("/app/nonexistent")).toBe(APP_NAME);
    expect(titleForPath("/app")).toBe(APP_NAME);
  });

  it("never leaves the page without a title", () => {
    for (const path of ["/app", "/app/", "/app/x/y/z", "/"]) {
      expect(titleForPath(path)).toContain(APP_NAME);
    }
  });
});
