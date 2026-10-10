// Render tests for the sign-in page's consent context.
//
// The repo has no jsdom, so `renderToString` is the renderer and the form
// cannot be submitted from a test. The behaviour worth pinning here is the one a
// connecting harness depends on: a visitor who is sent to sign-in *by an AI
// application* is told so, instead of being shown an unrelated-looking form and
// abandoning the connection. `lib/returnTo.test.ts` covers the path validation
// this copy is keyed on.

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { LoginPage } from "@/pages/public/LoginPage";

// The page reads the auth and toast contexts; neither contributes markup, and
// the real providers would only add state to the tree.
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ login: async () => {} }),
}));
vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
}));

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

/** Render the page as if a guard had bounced the visitor here from `from`. */
function render(from?: string): string {
  return renderToString(
    <MemoryRouter initialEntries={[{ pathname: "/login", state: from ? { from } : undefined }]}>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe("LoginPage consent context", () => {
  it("explains that an AI application is waiting when the consent route sent the visitor here", () => {
    const html = render("/app/authorize?client_id=x&code_challenge=y&scope=documents.read");
    expect(html).toContain("Sign in to connect");
    expect(html).toContain("An AI application sent you here");
  });

  it("keeps the ordinary sign-in copy for every other destination", () => {
    const html = render("/app/dashboard");
    expect(html).toContain("Welcome back");
    expect(html).not.toContain("An AI application sent you here");
  });

  it("keeps the ordinary copy when no destination was recorded", () => {
    expect(render()).toContain("Welcome back");
  });

  it("does not treat a hostile return path as a consent request", () => {
    // `safeReturnPath` rejects this before the copy is chosen, so an attacker
    // cannot make the sign-in page claim an application is waiting.
    const html = render("https://evil.example/app/authorize");
    expect(html).toContain("Welcome back");
    expect(html).not.toContain("An AI application sent you here");
  });
});
