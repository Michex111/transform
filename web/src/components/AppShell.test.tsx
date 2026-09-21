// Regression tests for the mobile bottom navigation's layout invariant.
//
// The bar renders the 4 primary destinations plus a "More" popover trigger, so
// it needs 5 grid tracks. It was `grid-cols-4`, which silently wrapped "More"
// onto a second row and doubled the bar's height (measured 134px instead of
// 63px) — 21% of a 640px viewport, and 37% in landscape. Nothing errored, so
// only a layout assertion catches it.
//
// Rendered through `renderToString` because the repo has no DOM test library.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 1, username: "tester", email: "t@example.com", is_active: true, created_at: "" },
    logout: vi.fn(),
  }),
}));

const { AppShell } = await import("@/components/AppShell");

// `renderToString` on an effectful tree logs a `useLayoutEffect` warning
// (react-router, motion). Expected here; silence just that message.
const realConsoleError = console.error;
beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) return;
    realConsoleError(...args);
  };
});
afterEach(() => {
  console.error = realConsoleError;
});

function renderShell(): string {
  return renderToString(
    <MemoryRouter initialEntries={["/app/files"]}>
      <AppShell />
    </MemoryRouter>,
  );
}

/** The markup of the mobile bottom bar only. */
function mobileNavHtml(html: string): string {
  const start = html.indexOf('aria-label="Mobile"');
  expect(start).toBeGreaterThan(-1);
  const open = html.lastIndexOf("<nav", start);
  const close = html.indexOf("</nav>", start);
  return html.slice(open, close);
}

/** The markup of the desktop sidebar nav only. */
function sidebarNavHtml(html: string): string {
  const start = html.indexOf('aria-label="Main"');
  expect(start).toBeGreaterThan(-1);
  const open = html.lastIndexOf("<nav", start);
  const close = html.indexOf("</nav>", start);
  return html.slice(open, close);
}

describe("AppShell mobile bottom nav", () => {
  it("has one grid track per item, so it never wraps to a second row", () => {
    const nav = mobileNavHtml(renderShell());
    const columns = Number(nav.match(/grid-cols-(\d+)/)?.[1]);
    const destinationLinks = (nav.match(/href="\/app\//g) ?? []).length;
    // "More" is a <button>, not a link.
    const items = destinationLinks + (/>More</.test(nav) ? 1 : 0);

    expect(destinationLinks).toBe(4);
    expect(items).toBe(5);
    expect(columns).toBe(items);
  });

  it("keeps all four primary destinations reachable", () => {
    const nav = mobileNavHtml(renderShell());
    for (const path of ["/app/dashboard", "/app/convert", "/app/queue", "/app/history"]) {
      expect(nav).toContain(`href="${path}"`);
    }
  });

  it("truncates nav labels instead of wrapping them", () => {
    // At 320px a 5-up cell is ~61px, narrower than "Dashboard" at this size.
    const nav = mobileNavHtml(renderShell());
    expect(nav).toContain("truncate");
  });

  it("respects the iOS home-indicator inset", () => {
    expect(renderShell()).toContain("env(safe-area-inset-bottom)");
  });
});

describe("AppShell desktop sidebar", () => {
  it("stays hidden below the lg breakpoint so it cannot crowd a phone", () => {
    const html = renderShell();
    const start = html.indexOf('aria-label="Main"');
    const open = html.lastIndexOf("<aside", start);
    const aside = html.slice(open, start);
    expect(aside).toContain("lg:flex");
    expect(aside).toContain("hidden");
  });

  it("lists all eight destinations", () => {
    const sidebar = sidebarNavHtml(renderShell());
    const links = sidebar.match(/href="\/app\//g) ?? [];
    expect(links).toHaveLength(8);
  });
});
