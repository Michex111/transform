// Render-level tests for the standalone "change password" card.
//
// The repo has no jsdom, so these render to a string and assert on the markup.
// That is what pins the two-step shape of this card, which is exactly the part
// that rots silently:
//
//   * the resting card must NOT contain a current-password box — that field
//     belongs to the confirmation dialog;
//   * the dialog must not be in the DOM at all while it is closed (the same
//     convention as `JobDetailsPanel`'s collapsed panel);
//   * the dialog's form must NOT be nested inside the resting form — invalid
//     HTML that browsers resolve unpredictably.
//
// The branches a submit produces are pinned through `PasswordSectionView`,
// whose whole state arrives as props; `renderToString` cannot fire a submit.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import type { PasswordSectionViewProps } from "@/pages/app/settings/PasswordSection";
import { MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";

vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
}));

// The card never calls this during a render — the request is triggered by a
// submit, which a string render cannot produce. Present so the import resolves
// and so an unexpected call fails here rather than reaching the network.
vi.mock("@/api/client", () => ({
  api: { changePassword: () => Promise.resolve(undefined) },
}));

const { PasswordSection, PasswordSectionView } = await import(
  "@/pages/app/settings/PasswordSection"
);

// Rendering an effectful tree through `renderToString` makes React log a
// `useLayoutEffect` warning (motion). Expected for an SSR smoke test; silence
// just that message and keep every real error visible.
const realConsoleError = console.error;
beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (
      typeof args[0] === "string" &&
      args[0].includes("useLayoutEffect does nothing on the server")
    ) {
      return;
    }
    realConsoleError(...args);
  };
});
afterEach(() => {
  console.error = realConsoleError;
});

/** React splits text and interpolation with comment markers, and escapes
 *  apostrophes in text; both are noise in an assertion. */
function clean(html: string): string {
  return html.replace(/<!-- -->/g, "").replace(/&#x27;/g, "'");
}

function render(node: ReactElement): string {
  return clean(renderToString(<MemoryRouter>{node}</MemoryRouter>));
}

/**
 * The deepest `<form>` nesting in the markup.
 *
 * `1` means every form is well-formed and unnested. A nested form would make
 * this `2` — the bug this file exists to catch, because a browser drops or
 * reparents the inner form and Enter-to-submit stops working in the dialog.
 */
function maxFormDepth(html: string): number {
  let depth = 0;
  let deepest = 0;
  for (const match of html.matchAll(/<form\b|<\/form>/g)) {
    depth += match[0] === "</form>" ? -1 : 1;
    deepest = Math.max(deepest, depth);
  }
  return deepest;
}

function formCount(html: string): number {
  return html.match(/<form\b/g)?.length ?? 0;
}

const noop = () => {};

/** The card with nothing typed and the dialog closed. */
function resting(overrides: Partial<PasswordSectionViewProps> = {}): ReactElement {
  return (
    <PasswordSectionView
      next=""
      confirm=""
      errors={{}}
      modalOpen={false}
      current=""
      busy={false}
      onNextChange={noop}
      onConfirmChange={noop}
      onCurrentChange={noop}
      onSubmit={noop}
      onConfirmSubmit={noop}
      onClose={noop}
      {...overrides}
    />
  );
}

describe("PasswordSectionView — the resting card", () => {
  const html = render(resting());

  it("asks for the new password twice and nothing else", () => {
    expect(html).toContain('id="new-password"');
    expect(html).toContain('id="confirm-password"');
    expect(html).toContain(">New password</label>");
    expect(html).toContain(">Confirm new password</label>");
    // The hint states the rule before the round trip, from the shared constant.
    expect(html).toContain(`At least ${MIN_PASSWORD_LENGTH} characters`);
  });

  it("has no current-password box: that field belongs to the dialog", () => {
    expect(html).not.toMatch(/autocomplete="current-password"/i);
    expect(html).not.toContain('id="current-password"');
    expect(html).not.toContain(">Current password</label>");
    // Both new-password boxes opt out of the browser's manager, so the
    // resting card's only autocomplete hints are the two below.
    expect(html.match(/autocomplete="new-password"/gi)).toHaveLength(2);
  });

  it("does not render the dialog while it is closed", () => {
    expect(html).not.toContain('role="dialog"');
    expect(html).not.toContain("Confirm your current password");
    // One form, unnested: the dialog's form does not exist yet.
    expect(formCount(html)).toBe(1);
    expect(maxFormDepth(html)).toBe(1);
  });

  it("announces that the submit opens a dialog", () => {
    // The button writes nothing; it opens a dialog, and a screen reader is told
    // what is about to appear rather than being surprised by it.
    expect(html).toContain('aria-haspopup="dialog"');
  });

  it("keeps the honest note about other sessions", () => {
    expect(html).toContain("Other devices stay signed in until their access token expires");
  });
});

describe("PasswordSectionView — local validation", () => {
  it("puts a short password's message under the new-password field", () => {
    const tooShort = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
    const html = render(resting({ next: "short", errors: { password: tooShort } }));

    expect(html).toContain(`id="new-password-error"`);
    expect(html).toContain(tooShort);
    // The error replaces the hint rather than stacking under it: "Use at least
    // 8 characters." under "At least 8 characters" is the form repeating
    // itself.
    expect(html).not.toContain('id="new-password-hint"');
    // The message is a live region on this path — no request is made, so no
    // toast fires, and nothing else would announce the refusal.
    expect(html).toMatch(/id="new-password-error"[^>]*role="alert"/);
    // It is not on the other field.
    expect(html).not.toContain('id="confirm-password-error"');
  });

  it("puts a mismatch under the confirmation field", () => {
    const mismatch = "The two passwords don't match.";
    const html = render(
      resting({ next: "correct horse", confirm: "correct hors", errors: { confirm: mismatch } }),
    );

    expect(html).toContain('id="confirm-password-error"');
    expect(html).toContain(mismatch);
    expect(html).not.toContain('id="new-password-error"');
  });
});

describe("PasswordSectionView — the confirmation dialog", () => {
  const html = render(resting({ modalOpen: true, next: "correct horse", confirm: "correct horse" }));

  it("is a real dialog labelled by its title", () => {
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-label="Confirm your current password"');
  });

  it("contains the current-password box, with the right autocomplete hint", () => {
    expect(html).toContain('id="current-password"');
    expect(html).toContain(">Current password</label>");
    expect(html).toMatch(/autocomplete="current-password"/i);
  });

  it("has its own form, and neither form is nested inside the other", () => {
    expect(formCount(html)).toBe(2);
    expect(maxFormDepth(html)).toBe(1);
  });

  it("offers a secondary cancel beside the submit", () => {
    expect(html).toContain(">Cancel</button>");
    expect(html).toContain("Update password");
  });

  it("says Updating… and locks the dialog while the request is in flight", () => {
    const busy = render(resting({ modalOpen: true, busy: true }));
    expect(busy).toContain("Updating…");

    // Escape and the backdrop go through the same guard as Cancel, so while
    // busy the dialog cannot be dismissed at all.
    const cancel = busy.match(/<button[^>]*>Cancel<\/button>/)?.[0] ?? "";
    expect(cancel).toContain('disabled=""');
    const submit = busy.match(/<button[^>]*>[^<]*Updating…[^<]*<\/button>/)?.[0] ?? "";
    expect(submit).toContain('disabled=""');
  });

  it("puts a wrong old password under the current-password field", () => {
    const html = render(
      resting({
        modalOpen: true,
        next: "newsecret",
        confirm: "newsecret",
        currentError: "Current password is incorrect.",
      }),
    );

    expect(html).toContain('id="current-password-error"');
    expect(html).toContain("Current password is incorrect.");
    // The new-password fields are left untouched so the retry is one field, not
    // three.
    expect(html.match(/value="newsecret"/g)).toHaveLength(2);
  });

  it("shows any other failure inline in the dialog, unattached to a field", () => {
    const html = render(resting({ modalOpen: true, formError: "Failed to fetch" }));

    expect(html).toContain("Failed to fetch");
    expect(html).not.toContain('id="current-password-error"');
    // No `role="alert"`: the toast that fires with this message is the live
    // announcement, and two alerts for one failure is noise.
    expect(html).not.toMatch(/class="text-sm text-error"[^>]*role="alert"/);
  });
});

describe("PasswordSection", () => {
  it("renders the resting card on its own, with no dialog", () => {
    const html = render(<PasswordSection />);

    expect(html).toContain('id="new-password"');
    expect(html).not.toContain('role="dialog"');
    expect(html).not.toMatch(/autocomplete="current-password"/i);
    expect(maxFormDepth(html)).toBe(1);
  });
});
