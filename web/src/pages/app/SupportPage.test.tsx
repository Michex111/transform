// Regression test for the Support page's external links.
//
// The docs and status links were same-origin paths (`/docs`, `/health`). In
// production the SPA is a *separate* static site from the API, so those loaded
// the SPA itself and the router's `path="*"` catch-all rendered an empty page.
// The API serves both paths at its root, so they must be built from the API
// origin (the configured `VITE_API_BASE_URL` without its `/api` suffix).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";

const ABSOLUTE_API_BASE = "https://transform-api-7b3g.onrender.com/api";
const API_ORIGIN = "https://transform-api-7b3g.onrender.com";

// `renderToString` on an effectful tree logs a `useLayoutEffect` warning
// (motion). Expected for an SSR smoke test; silence just that message.
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

/** Render the page with `VITE_API_BASE_URL` stubbed (the origin is baked in at module load). */
async function renderSupport(apiBase?: string): Promise<string> {
  vi.resetModules();
  if (apiBase === undefined) {
    vi.unstubAllEnvs();
  } else {
    vi.stubEnv("VITE_API_BASE_URL", apiBase);
  }
  const { SupportPage } = await import("@/pages/app/SupportPage");
  return renderToString(<SupportPage />);
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

// Importing this page pulls in the whole motion/icon/UI graph; the very first
// import in the worker can exceed the default 5s test timeout, so warm it up
// while the file is being collected and allow a generous timeout below.
await import("@/pages/app/SupportPage");

/** The dynamic import + render needs longer than the 5s default on a cold worker. */
const RENDER_TIMEOUT_MS = 20_000;

describe("SupportPage links", () => {
  it("points the docs and status links at the API origin, not the SPA's own origin", async () => {
    const html = await renderSupport(ABSOLUTE_API_BASE);

    expect(html).toContain(`href="${API_ORIGIN}/docs"`);
    expect(html).toContain(`href="${API_ORIGIN}/health"`);
    // The SPA origin would serve the SPA shell (an empty page), so the bare
    // same-origin paths must be gone.
    expect(html).not.toContain('href="/docs"');
    expect(html).not.toContain('href="/health"');
  }, RENDER_TIMEOUT_MS);

  it("keeps the external-link safety attributes", async () => {
    const html = await renderSupport(ABSOLUTE_API_BASE);

    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noreferrer"');
  });

  it("uses the dev proxy prefix when the API is same-origin", async () => {
    // Local dev keeps requests same-origin through the Vite proxy, which
    // forwards `/docs` and `/health` (see vite.config.ts) so the links resolve
    // to the API instead of the SPA router.
    const html = await renderSupport("/api");

    expect(html).toContain('href="/docs"');
    expect(html).toContain('href="/health"');
  });
});
