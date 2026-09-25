// Render-level tests for the account settings sections.
//
// The repo has no jsdom, so these render to a string and assert on the HTML.
// That is enough for the things that silently rot: which branch of the phone
// state machine is showing, whether a number reaches the page raw or formatted,
// and whether the tab/panel ARIA wiring still lines up. Interactive flows
// (uploading, the countdown, the delete dialog) are covered by the pure rules
// they delegate to in `lib/phone.ts` and `lib/verificationCode.ts`.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";

// `vi.hoisted` so the mock factories below can read it — `vi.mock` calls are
// hoisted above the imports, and a plain module-level `let` would still be in
// its temporal dead zone when the factory runs.
const state = vi.hoisted(() => ({
  user: null as Record<string, unknown> | null,
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: state.user,
    setUser: () => {},
    refreshUser: async () => {},
    logout: () => {},
    api: {},
  }),
}));

vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
}));

// Only `listApiKeys` could ever run, and even that cannot: `renderToString`
// does not run effects. The rest is absent on purpose so an unexpected call
// fails loudly here instead of reaching the network.
vi.mock("@/api/client", () => ({
  api: { listApiKeys: () => Promise.resolve({ keys: [] }) },
}));

const { SettingsPage } = await import("@/pages/app/SettingsPage");
const { PhoneSection } = await import("@/pages/app/settings/PhoneSection");
const { ProfileSection } = await import("@/pages/app/settings/ProfileSection");

// Rendering an effectful tree through `renderToString` makes React log a
// `useLayoutEffect` warning (react-router, motion). Expected for an SSR smoke
// test; silence just that message and keep every real error visible.
const realConsoleError = console.error;
beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
      return;
    }
    realConsoleError(...args);
  };
});
afterEach(() => {
  console.error = realConsoleError;
});

/** React splits text and interpolation with comment markers; drop them. */
function stripReactComments(html: string): string {
  return html.replace(/<!-- -->/g, "");
}

/** A signed-in account. Every profile field is optional on the wire. */
function user(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 1,
    username: "ada",
    email: "ada@example.com",
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    email_verified: true,
    first_name: "Ada",
    last_name: "Lovelace",
    display_name: "Ada Lovelace",
    initials: "AL",
    avatar_url: null,
    phone_number: null,
    phone_verified: false,
    ...overrides,
  };
}

function render(node: ReactElement, path = "/app/settings"): string {
  return stripReactComments(
    renderToString(<MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>),
  );
}

/** The first element carrying `role`, as an HTML string. */
function elementWithRole(html: string, role: string): string {
  return html.match(new RegExp(`<\\w+[^>]*role="${role}"[^>]*>`))?.[0] ?? "";
}

describe("PhoneSection", () => {
  it("shows the verified treatment once a number is confirmed", () => {
    state.user = user({ phone_number: "+14155552671", phone_verified: true });
    const html = render(<PhoneSection />);

    expect(html).toContain("Verified");
    expect(html).toContain("Remove phone");
    // The verified state is not an input: no way to send a code to a number
    // that is already confirmed.
    expect(html).not.toContain("Send code");
  });

  it("shows the formatted number, never the stored E.164 string", () => {
    state.user = user({ phone_number: "+14155552671", phone_verified: true });
    const html = render(<PhoneSection />);

    expect(html).toContain("+1 415 555 2671");
    // The raw value must not leak into the UI: this is what a user checks
    // against their own phone.
    expect(html).not.toContain("+14155552671");
  });

  it("asks for a number when the account has none", () => {
    state.user = user();
    const html = render(<PhoneSection />);

    expect(html).toContain("Send code");
    expect(html).toContain('placeholder="+14155552671"');
    expect(html).toContain("International format");
    expect(html).not.toContain("Verified");
  });

  it("asks for the code when a number is on file but unverified", () => {
    state.user = user({ phone_number: "+14155552671", phone_verified: false });
    const html = render(<PhoneSection />);

    expect(html).toContain("Verification code");
    expect(html).toContain("Resend code");
    expect(html).toContain("Change number");
    expect(html).toContain("+1 415 555 2671");
    // The entry form is replaced by the code step, not shown alongside it.
    expect(html).not.toContain("Send code");
  });

  it("autocompletes the code from an SMS where the platform supports it", () => {
    state.user = user({ phone_number: "+14155552671", phone_verified: false });
    const html = render(<PhoneSection />);

    // Case-insensitive because `renderToString` prints some attributes with the
    // React prop's casing (`autoComplete`). HTML attribute names are
    // case-insensitive, so the browser sees the attribute either way.
    expect(html).toMatch(/autocomplete="one-time-code"/i);
    expect(html).toMatch(/maxlength="6"/i);
    expect(html).toMatch(/inputmode="numeric"/i);
  });
});

describe("ProfileSection", () => {
  it("renders the initials tile when there is no picture", () => {
    state.user = user();
    const html = render(<ProfileSection />);

    expect(html).toContain(">AL</span>");
    expect(html).not.toContain("<img");
  });

  it("renders the picture when the server sends one", () => {
    state.user = user({ avatar_url: "data:image/webp;base64,AAAA" });
    const html = render(<ProfileSection />);

    expect(html).toContain('src="data:image/webp;base64,AAAA"');
    expect(html).not.toContain(">AL</span>");
  });

  it("seeds the name fields from the account", () => {
    state.user = user();
    const html = render(<ProfileSection />);

    expect(html).toContain('value="Ada"');
    expect(html).toContain('value="Lovelace"');
    expect(html).toMatch(/autocomplete="given-name"/i);
    expect(html).toMatch(/autocomplete="family-name"/i);
  });

  it("keeps the username read-only and explains why", () => {
    state.user = user();
    const html = render(<ProfileSection />);

    expect(html).toContain('value="ada"');
    expect(html).toContain("disabled");
    expect(html).toContain("This is the name you sign in with");
  });

  it("keeps the unverified-email warning and its resend button", () => {
    state.user = user({ email_verified: false });
    const html = render(<ProfileSection />);

    expect(html).toContain("Resend link");
    expect(html).toContain("not verified yet");
    expect(html).not.toContain("Email verified");
  });

  it("shows the verified state otherwise", () => {
    state.user = user();
    const html = render(<ProfileSection />);

    expect(html).toContain("Email verified");
    expect(html).not.toContain("Resend link");
  });
});

describe("SettingsPage tabs", () => {
  beforeEach(() => {
    state.user = user({ phone_number: "+14155552671", phone_verified: true });
  });

  it("renders four tabs with exactly one selected", () => {
    const html = render(<SettingsPage />);

    expect(html.match(/role="tab"/g)).toHaveLength(4);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(html.match(/aria-selected="false"/g)).toHaveLength(3);
    expect(html).toContain('aria-label="Account settings"');
  });

  it("opens the tab the query string asks for", () => {
    const html = render(<SettingsPage />, "/app/settings?tab=phone");

    expect(html).toContain('role="tabpanel"');
    expect(html).toContain("Remove phone");
    // Only the selected panel is mounted, so the profile fields are absent.
    expect(html).not.toContain("First name");
  });

  it("defaults to the profile tab", () => {
    const html = render(<SettingsPage />);

    expect(html).toContain("First name");
    expect(html).not.toContain("Remove phone");
  });

  it("shows the standalone password card beside the profile, not behind a tab", () => {
    const html = render(<SettingsPage />);

    // Both cards on one screen: the password form is no longer something the
    // user has to find in a tab, and the profile fields are not hidden while
    // they change a password.
    expect(html).toContain("First name");
    expect(html).toContain(">New password</label>");
    expect(html).toContain(">Confirm new password</label>");
    expect(html).toContain("Update password");
    // The current password is asked for in the dialog, so it is not on screen
    // until the user has submitted a new one.
    expect(html).not.toMatch(/autocomplete="current-password"/i);
    // And there is no tab that would render the form a second time.
    expect(html).not.toContain('id="settings-tab-password"');
  });

  it("renders the password card for the retired ?tab=password address", () => {
    // An old bookmark must not dead-end. It resolves to profile (see
    // `settingsTabs.ts`), which is the screen that carries the password card.
    const html = render(<SettingsPage />, "/app/settings?tab=password");

    expect(html).toContain('role="tabpanel"');
    expect(html).toContain('id="settings-panel-profile"');
    expect(html).toContain("First name");
    expect(html).toContain(">New password</label>");
  });

  it("falls back to the profile tab for a tab that does not exist", () => {
    const html = render(<SettingsPage />, "/app/settings?tab=bogus");

    expect(html).toContain("First name");
    expect(html).not.toContain("Remove phone");
  });

  it("selects the tab named in the query string", () => {
    const html = render(<SettingsPage />, "/app/settings?tab=phone");

    const phoneTab = html.match(/<button[^>]*id="settings-tab-phone"[^>]*>/)?.[0] ?? "";
    expect(phoneTab).toContain('aria-selected="true"');
    expect(phoneTab).toContain('tabindex="0"');

    // Roving tabindex: every other tab is out of the tab order.
    const profileTab = html.match(/<button[^>]*id="settings-tab-profile"[^>]*>/)?.[0] ?? "";
    expect(profileTab).toContain('aria-selected="false"');
    expect(profileTab).toContain('tabindex="-1"');
  });

  it("wires each tab to the panel it opens", () => {
    const html = render(<SettingsPage />, "/app/settings?tab=phone");

    const phoneTab = html.match(/<button[^>]*id="settings-tab-phone"[^>]*>/)?.[0] ?? "";
    expect(phoneTab).toContain('aria-controls="settings-panel-phone"');

    // The panel the selected tab names must be the panel that is rendered,
    // labelled by that same tab. This is the part that rots silently.
    const panel = elementWithRole(html, "tabpanel");
    expect(panel).toContain('id="settings-panel-phone"');
    expect(panel).toContain('aria-labelledby="settings-tab-phone"');
    expect(html.match(/role="tabpanel"/g)).toHaveLength(1);
  });

  it("keeps the panel id consistent with the tab's aria-controls for a tab that is not first", () => {
    const html = render(<SettingsPage />, "/app/settings?tab=danger");

    const dangerTab = html.match(/<button[^>]*id="settings-tab-danger"[^>]*>/)?.[0] ?? "";
    expect(dangerTab).toContain('aria-controls="settings-panel-danger"');
    expect(elementWithRole(html, "tabpanel")).toContain('id="settings-panel-danger"');
    expect(html).toContain('aria-labelledby="settings-tab-danger"');
    expect(html).toContain("Danger zone");
  });

  it("offers a way to sign out beside the delete action", () => {
    // An account page must not make deletion the only exit.
    const html = render(<SettingsPage />, "/app/settings?tab=danger");

    expect(html).toContain("Delete account");
    expect(html).toContain("Sign out of this device");
    // The old confirm-then-"not available yet" dead end is gone.
    expect(html).not.toContain("isn't available yet");
  });
});
