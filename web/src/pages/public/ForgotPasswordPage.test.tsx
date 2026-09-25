// Render smoke tests for the "forgot password" page.
//
// The repo has no jsdom, so `renderToString` is the only renderer: effects and
// events do not run. `ForgotPasswordPage` (the route component) is therefore
// asserted for its initial panel, and the confirmation panel — which can only
// appear after a submit — is asserted against `ForgotPasswordView` directly.
// The request shape is covered in `api/client.test.ts`.

import type { ReactElement } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { ForgotPasswordPage, ForgotPasswordView } from "@/pages/public/ForgotPasswordPage";

// The page's error path calls `useToast`; the real provider is unnecessary here
// and would only add state to the tree.
vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
}));

// Nothing can call this under `renderToString` (no effects, no events), so it is
// absent-by-design: an unexpected call fails loudly here instead of reaching the
// network the brief says may not have the endpoint yet.
vi.mock("@/api/client", () => ({
  api: { forgotPassword: () => Promise.resolve({ message: "Sent." }) },
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

function render(node: ReactElement, path = "/forgot-password"): string {
  return renderToString(<MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>);
}

/** The view with a confirmation panel already showing. */
function sentView(message = ""): ReactElement {
  return (
    <ForgotPasswordView
      email="ada@example.com"
      onEmailChange={() => {}}
      onSubmit={() => {}}
      busy={false}
      sent={{ email: "ada@example.com", message }}
      onTryAgain={() => {}}
      onBackToSignIn={() => {}}
    />
  );
}

describe("ForgotPasswordPage", () => {
  it("renders the email form", () => {
    const html = render(<ForgotPasswordPage />);

    expect(html).toContain("Reset your password");
    expect(html).toContain("Send reset link");
    // Type and autocomplete are what make a password manager offer the address
    // it already has, rather than making the user retype it.
    expect(html).toMatch(/type="email"/i);
    expect(html).toMatch(/autocomplete="email"/i);
    expect(html).toMatch(/required/);
  });

  it("offers a way back to sign in", () => {
    expect(render(<ForgotPasswordPage />)).toContain('href="/login"');
  });

  it("does not claim a mail was sent before anything was submitted", () => {
    // The confirmation is only reachable after the request is accepted; showing
    // it up front would tell the user something the page cannot know.
    expect(render(<ForgotPasswordPage />)).not.toContain("Check your email");
  });
});

describe("ForgotPasswordView confirmation", () => {
  it("shows the submitted address and the ways forward", () => {
    const html = render(sentView());

    expect(html).toContain("Check your email");
    expect(html).toContain("ada@example.com");
    // A long address must wrap instead of forcing the card wider or being
    // clipped — hence the class assertion.
    expect(html).toContain("break-all font-mono");
    expect(html).toContain("Back to sign in");
    // The server rate-limits silently, so re-submitting is a valid recovery;
    // the user must be able to get back to the form.
    expect(html).toContain("Try again");
  });

  it("tells the user to wait a minute and to check spam", () => {
    const html = render(sentView());

    expect(html).toContain("minute or two");
    expect(html).toContain("spam");
  });

  it("uses the server's message when it sends one", () => {
    expect(render(sentView("On its way."))).toContain("On its way.");
  });

  it("falls back to conditional wording when the 202 body has no message", () => {
    // The endpoint answers identically for every address, so the copy must never
    // assert that a mail exists — only that one was sent *if* the account does.
    const html = render(sentView());

    expect(html).toContain("If an account with that email address exists");
  });
});
