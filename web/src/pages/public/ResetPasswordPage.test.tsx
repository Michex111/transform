// Render smoke tests for the password-reset page.
//
// The repo has no jsdom, so `renderToString` is the only renderer: effects and
// events do not run, which means a submit (and therefore a state transition)
// cannot be triggered from a test. `ResetPasswordPage` is asserted for the
// panels a token alone determines — form vs. missing — and `ResetPasswordView`
// is asserted for the panels that follow a submit (dead token, success, and the
// per-field validation errors). The request shape is covered in
// `api/client.test.ts` and the decisions themselves in
// `lib/passwordReset.test.ts`.

import type { ReactElement } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ResetPasswordPage, ResetPasswordView } from "@/pages/public/ResetPasswordPage";
import { PASSWORD_RESET_INVALID_MESSAGE } from "@/lib/passwordReset";

// The page's unexpected-failure path calls `useToast`; the real provider is
// unnecessary here and would only add state to the tree.
vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
}));

// Nothing can call this under `renderToString` (no effects, no events), so it is
// absent-by-design: an unexpected call fails loudly here instead of reaching the
// network the brief says may not have the endpoint yet.
vi.mock("@/api/client", () => ({
  api: { resetPassword: () => Promise.resolve({ ok: true, username: "ada", message: "Done." }) },
}));

// react-router's <Link> and motion use layout effects, which log a benign
// "does nothing on the server" warning under renderToString. Silenced so the
// suite output stays readable; anything else still surfaces.
const SSR_WARNING = "useLayoutEffect does nothing on the server";
const originalError = console.error;
beforeAll(() => {
  console.error = (...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].includes(SSR_WARNING)) return;
    originalError(...args);
  };
});
afterAll(() => {
  console.error = originalError;
});

function render(node: ReactElement, path: string): string {
  return renderToString(<MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>);
}

/** The pure view, rendered at the address a reset link would carry. */
function renderView(overrides: Partial<Parameters<typeof ResetPasswordView>[0]> = {}): string {
  return render(
    <ResetPasswordView
      phase="form"
      password=""
      confirm=""
      onPasswordChange={() => {}}
      onConfirmChange={() => {}}
      errors={{}}
      busy={false}
      onSubmit={() => {}}
      message=""
      onContinue={() => {}}
      onRequestNewLink={() => {}}
      {...overrides}
    />,
    "/reset-password?token=tok-abc",
  );
}

describe("ResetPasswordPage", () => {
  it("renders the form when the address carries a token", () => {
    const html = render(<ResetPasswordPage />, "/reset-password?token=tok-abc");

    expect(html).toContain("Choose a new password");
    expect(html).toContain("New password");
    expect(html).toContain("Confirm new password");
    expect(html).toContain("Update password");
    // Both fields must be `new-password` so a password manager offers to
    // generate one instead of trying to fill the current one.
    expect(html).toMatch(/autocomplete="new-password"/i);
    expect(html).not.toContain("Link not valid");
  });

  it("renders the missing-link state when no token is in the address", () => {
    const html = render(<ResetPasswordPage />, "/reset-password");

    expect(html).toContain("Link not valid");
    expect(html).toContain("no reset token was included");
    expect(html).toContain("Request a new link");
    // The recovery action is a real `<button>`, not a `Link` wrapping a
    // `Button`: an anchor must not contain an interactive element, and the
    // nesting gave one action two tab stops. Pinned so it cannot regress.
    expect(html).not.toContain('href="/forgot-password"');
    expect(html).toMatch(/<button[^>]*type="button"/);
    expect(html).toContain('href="/login"');
    // Nothing to submit, so no password box is offered.
    expect(html).not.toContain("Confirm new password");
  });
});

describe("ResetPasswordView validation", () => {
  it("renders the short-password message under the new password field", () => {
    const html = renderView({ errors: { password: "Use at least 8 characters." } });

    expect(html).toContain("Use at least 8 characters.");
    expect(html).toContain('id="new-password-error"');
    // The message has to be programmatically tied to its input, not just
    // positioned below it.
    expect(html).toMatch(/aria-describedby="new-password-error"/i);
    expect(html).toMatch(/aria-invalid="true"/i);
    // And it must be ANNOUNCED. No request is made on a validation failure, so
    // no toast fires and `role="alert"` is the only live announcement there
    // is — without it a screen-reader user is told nothing at all.
    expect(html).toMatch(/<p[^>]*id="new-password-error"[^>]*role="alert"|<p[^>]*role="alert"[^>]*id="new-password-error"/);
  });

  it("renders the mismatch message under the confirmation field only", () => {
    const html = renderView({ errors: { confirm: "The two passwords don't match." } });

    expect(html).toMatch(/The two passwords don(?:&#x27;|')t match\./);
    expect(html).toContain('id="confirm-password-error"');
    expect(html).toMatch(/aria-describedby="confirm-password-error"/i);
    expect(html).not.toContain('id="new-password-error"');
  });
});

describe("ResetPasswordView outcomes", () => {
  it("renders the dead-token panel with a way to get a new link", () => {
    const html = renderView({ phase: "dead" });

    expect(html).toContain("Link not valid");
    expect(html).toContain(PASSWORD_RESET_INVALID_MESSAGE);
    expect(html).toContain("Request a new link");
    expect(html).not.toContain('href="/forgot-password"');
    expect(html).toMatch(/<button[^>]*type="button"/);
    // Retrying the same token is pointless, so no password box is offered.
    expect(html).not.toContain("Choose a new password");
  });

  it("renders the success panel with the server's message and a sign-in button", () => {
    const html = renderView({
      phase: "done",
      message: "Your password has been updated. Sign in with your new password.",
    });

    expect(html).toContain("Password updated");
    expect(html).toContain("Your password has been updated. Sign in with your new password.");
    expect(html).toContain("Continue to sign in");
  });

  it("falls back to the contract wording when the success body has no message", () => {
    expect(renderView({ phase: "done" })).toContain("Sign in with your new password.");
  });

  it("is honest that the reset does not sign other devices out", () => {
    // The deployment issues stateless JWTs with no server-side token store, so
    // an implied global sign-out would be false.
    const html = renderView({ phase: "done" });

    expect(html).toContain("does not sign you out");
    expect(html).toContain("access token expires");
    expect(html).not.toContain("signed out everywhere");
  });
});
