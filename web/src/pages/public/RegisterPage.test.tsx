// Render smoke tests for the sign-up form.
//
// The repo has no jsdom, so `renderToString` is the only renderer and a submit
// cannot be fired from a test. What this can still pin is the thing the user
// reported — that the username rule is *visible* before submitting, so the
// mistake is answered in the form rather than by a round trip that used to come
// back saying "String should have at least 3 characters". The validation and
// API-message placement themselves are covered in `lib/registerForm.test.ts`
// and `lib/apiErrors.test.ts`.

import type { ReactElement } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { RegisterPage } from "@/pages/public/RegisterPage";
import { MIN_PASSWORD_LENGTH, MIN_USERNAME_LENGTH } from "@/lib/registerForm";

// The page reads the auth context and the toast context; neither contributes
// markup, and the real providers would only add state to the tree.
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ register: async () => ({ email: "ada@example.com" }) }),
}));
vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: () => {}, error: () => {}, info: () => {}, toast: () => {} }),
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

function render(node: ReactElement): string {
  return renderToString(<MemoryRouter initialEntries={["/register"]}>{node}</MemoryRouter>);
}

describe("RegisterPage", () => {
  const html = render(<RegisterPage />);

  it("states the username rule before the form is submitted", () => {
    // The constraint used to be discoverable only by failing: the API answered
    // a two-character username with a sentence about a "String".
    expect(html).toContain(`At least ${MIN_USERNAME_LENGTH} characters`);
    expect(html).toContain("Username");
    expect(html).toContain("At least 8 characters");
  });

  it("does not mention Python types or schema vocabulary", () => {
    for (const word of ["String should", "Field required", "Input should", "value_error"]) {
      expect(html).not.toContain(word);
    }
  });

  it("describes each hinted field from its own hint element", () => {
    // Now that `Field` links a hint via `aria-describedby`, the rule is read
    // out with the input rather than sitting under it as visual-only text.
    expect(html).toContain('id="username-hint"');
    expect(html).toMatch(/aria-describedby="username-hint"/);
    expect(html).toContain('id="password-hint"');
    expect(html).toMatch(/aria-describedby="password-hint"/);
  });

  it("shows no failure until a submit fails", () => {
    // Nothing has failed yet, so nothing may be announced or marked invalid —
    // a form that greets the user with red text is worse than one that is quiet.
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain('aria-invalid="true"');
  });

  it("keeps the password minimum out of the username field's hint", () => {
    // Guards against the two constants being crossed over.
    expect(html).toContain(`At least ${MIN_PASSWORD_LENGTH} characters`);
    expect(html).not.toContain(`At least ${MIN_PASSWORD_LENGTH} characters. It can't be changed`);
  });
});
