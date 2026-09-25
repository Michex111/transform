// Markup tests for the account dropdown.
//
// Rendered through `renderToString` because the repo has no DOM test library
// (`environment: "node"`, no jsdom/RTL): effects never run, so the trigger can
// never be clicked. That is why the component takes `defaultOpen` — the panel's
// entries, roles and destructive treatment are only assertable if the panel is
// in the first render, and asserting them against a hand-copied fixture would
// keep passing after the real panel changed.

import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { UserResponse } from "@/api/types";
import { SIDEBAR_MENU } from "@/lib/profileMenu";

// `vi.mock` factories are hoisted above these declarations, so the user the mock
// answers with lives in a holder that is hoisted with them.
const auth = vi.hoisted(() => ({ user: null as UserResponse | null }));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: auth.user,
    logout: vi.fn(),
    setUser: vi.fn(),
    refreshUser: vi.fn(),
    isAuthenticated: Boolean(auth.user),
    isLoading: false,
    api: { deleteHistoryPreview: vi.fn(), deleteHistoryRange: vi.fn() },
  }),
}));

vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ toast: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

vi.mock("@/jobs/JobsContext", () => ({
  useJobs: () => ({
    jobs: [],
    refresh: vi.fn(),
    addJob: vi.fn(),
    updateJob: vi.fn(),
    removeJob: vi.fn(),
  }),
}));

const { ProfileMenu } = await import("@/components/ProfileMenu");

const ADA: UserResponse = {
  id: 7,
  username: "ada",
  email: "ada@example.com",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  display_name: "Ada Lovelace",
  initials: "AL",
  avatar_url: "data:image/webp;base64,AAAA",
};

const NO_PICTURE: UserResponse = { ...ADA, avatar_url: null };

// `renderToString` on an effectful tree logs a `useLayoutEffect` warning
// (react-router, motion). Expected here; silence just that message.
const realConsoleError = console.error;
beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
      return;
    }
    realConsoleError(...args);
  };
  auth.user = ADA;
});
afterEach(() => {
  console.error = realConsoleError;
});

function render(props: Partial<ComponentProps<typeof ProfileMenu>> = {}): string {
  return renderToString(
    <MemoryRouter>
      <ProfileMenu {...props} />
    </MemoryRouter>,
  );
}

function countOf(html: string, needle: string): number {
  return html.split(needle).length - 1;
}

describe("ProfileMenu trigger", () => {
  it("names the account and advertises a menu that starts closed", () => {
    const html = render();
    expect(html).toContain('aria-haspopup="menu"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('aria-label="Account menu for Ada Lovelace"');
    // Closed means closed: no menu in the DOM to be announced or tabbed into.
    expect(html).not.toContain('role="menu"');
  });

  it("renders the picture when the account has one and initials when it does not", () => {
    expect(render()).toContain('data-variant="image"');
    auth.user = NO_PICTURE;
    expect(render()).toContain('data-variant="initials"');
  });

  it("prints the name and email beside the avatar only when asked", () => {
    const identity = render({ showIdentity: true });
    expect(identity).toContain("ada@example.com");
    // The trigger's aria-label names the account either way (a screen reader
    // needs it), so the *visible* name is what `showIdentity` controls: once in
    // the label, once in the button.
    expect(countOf(identity, "Ada Lovelace")).toBe(2);

    const avatarOnly = render();
    expect(avatarOnly).not.toContain("ada@example.com");
    expect(countOf(avatarOnly, "Ada Lovelace")).toBe(1);
  });
});

describe("ProfileMenu panel", () => {
  it("renders the account links as menu items", () => {
    const html = render({ defaultOpen: true });
    expect(html).toContain('role="menu"');
    expect(countOf(html, 'role="menuitem"')).toBe(3);
    // "Account settings", not the sidebar's bare "Settings": in a menu opened
    // from the user's own avatar, sitting next to "Billing", the shorter label
    // reads as app settings rather than the page it opens. See lib/profileMenu.ts.
    for (const label of ["Account settings", "Billing", "Support"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain('href="/app/settings"');
  });

  it("has no logout entry unless the caller offers one", () => {
    // The same component either way, one prop. No placement passes false today,
    // but the option is the mechanism that expresses "may this menu sign you
    // out?" as a placement decision — see `lib/profileMenu.ts`.
    expect(render({ defaultOpen: true, showLogout: false })).not.toContain("Log out");

    const withLogout = render({ defaultOpen: true, showLogout: true });
    expect(countOf(withLogout, "Log out")).toBe(1);
    expect(withLogout.indexOf("Log out")).toBeGreaterThan(withLogout.indexOf("Support"));
  });

  it("drops the app links when asked, keeping the account entry", () => {
    // The sidebar's panel: the rail already lists Billing and Support.
    const html = render({ defaultOpen: true, includeAppLinks: false });
    expect(html).toContain("Account settings");
    expect(html).not.toContain("Billing");
    expect(html).not.toContain("Support");
    expect(countOf(html, 'role="menuitem"')).toBe(1);
  });

  it("renders the desktop sidebar's panel, with no repeated identity", () => {
    // `AppShell` renders this panel closed, so this is the only place the
    // desktop menu's contents can be asserted — and it is what stops the two
    // parts drifting: if `SIDEBAR_MENU` changes, this fails.
    //
    // `showIdentity` is part of the desktop placement too (it is what makes the
    // trigger the name + email row) and the sidebar passes it, so it belongs in
    // the render for the last assertion to mean anything: the panel must not
    // print the address a second time, right below the trigger that already
    // shows it.
    const html = render({ ...SIDEBAR_MENU, showIdentity: true, defaultOpen: true });
    expect(countOf(html, 'role="menuitem"')).toBe(3);
    for (const entry of ["Account settings", "Delete last 24 hours", "Log out"]) {
      expect(html).toContain(entry);
    }
    // The two destinations the rail already lists stay out of the panel.
    for (const dropped of ["Billing", "Support"]) {
      expect(html).not.toContain(dropped);
    }
    // Exactly one occurrence: the sidebar trigger shows the address, and the
    // panel must not print it again directly beneath it. The earlier version of
    // this test asserted `not.toContain`, which the trigger alone made false.
    expect(countOf(html, "ada@example.com")).toBe(1);
  });

  it("offers the history purge only where the caller allows it", () => {
    expect(render({ defaultOpen: true, allowHistoryDelete: false })).not.toContain(
      "Delete last 24 hours",
    );
    expect(render({ defaultOpen: true, allowHistoryDelete: true })).toContain(
      "Delete last 24 hours",
    );
  });

  it("does not open the delete dialog just by opening the menu", () => {
    // The count is fetched on the click, not on open: a destructive confirmation
    // must never appear before the user asked for it, and never with a count the
    // client made up.
    const html = render({ defaultOpen: true, allowHistoryDelete: true });
    expect(html).not.toContain('role="dialog"');
    expect(html).not.toContain("permanently removes");
  });
});
