/**
 * `SPA_ROUTES` must stay in sync with the route table in `src/App.tsx`.
 *
 * This is a documented, already-hit hazard: the static host only serves a
 * client-side route if a real file exists at that path, so a route added to
 * `App.tsx` without a matching stub **404s on a hard load** — direct
 * navigation, a refresh, an external redirect, or a link opened from an email.
 * That last case is exactly what `/verify-email` is for.
 *
 * A pure unit test cannot import `App.tsx` here (it is JSX, compiled under a
 * different tsconfig), so this reads the two files as text. That is enough:
 * both lists are literal, and the assertion is about the *set* of paths rather
 * than any runtime behaviour.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SPA_ROUTES } from "./spa-route-stubs";

const APP_TSX = fileURLToPath(new URL("../src/App.tsx", import.meta.url));

/**
 * Every static route declared in `App.tsx`.
 *
 * Dynamic patterns are excluded on purpose:
 *
 *  - `/:slug` is the public format-route dispatcher and has no single path.
 *  - `/app/:id`-style patterns cannot be stubbed per-value.
 *
 * A wildcard (`*`) was never stubbable either, and is the catch-all rather than
 * a route a user navigates to.
 */
function staticRoutePaths(appSource: string): string[] {
  const paths = [...appSource.matchAll(/<Route\s+path="([^"]+)"/g)].map((match) => match[1]);

  return paths.filter(
    (path) => path.startsWith("/") && !path.includes(":") && !path.includes("*"),
  );
}

describe("SPA route stubs", () => {
  const appSource = readFileSync(APP_TSX, "utf8");
  const declared = staticRoutePaths(appSource);

  it("finds the routes in App.tsx at all", () => {
    // Guards against the regex silently matching nothing (which would make
    // every assertion below vacuously true) if the route syntax changes.
    expect(declared.length).toBeGreaterThan(8);
    expect(declared).toContain("/verify-email");
  });

  it.each([
    "login",
    "register",
    "verify-email",
    "forgot-password",
    "reset-password",
    "pricing",
    "security",
    "convert",
  ])("stubs the public route /%s", (route) => {
    expect(SPA_ROUTES).toContain(`/${route}`);
  });

  it("stubs every static route App.tsx declares", () => {
    // `/` is exempt: `index.html` already serves it.
    const missing = declared.filter((path) => path !== "/" && !SPA_ROUTES.includes(path as never));

    expect(missing).toEqual([]);
  });

  it("declares no stub for a path App.tsx does not route", () => {
    // A stale stub is harmless but it hides a rename: the old path keeps
    // serving the shell and renders the 404 page instead of a real 404.
    const stale = SPA_ROUTES.filter((path) => !declared.includes(path));

    expect(stale).toEqual([]);
  });
});
