// Render smoke tests for the single dynamic public route.
//
// The SPA is client-only, so the server renderer is used purely as a crash
// detector: it exercises slug → page selection for the shapes that actually
// reach `/:slug` (including multi-dot extensions and malformed input) without
// needing a DOM or a running API. Effects do not run under `renderToString`,
// so each page renders its loading/empty branches — which is exactly what we
// want to prove never throws.

import type { ReactElement } from "react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FormatRoutePage } from "@/pages/public/FormatRoutePage";
import { LandingPage } from "@/pages/public/LandingPage";

// react-router's <Link> calls useLayoutEffect, which logs a benign
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

function renderRoute(path: string) {
  // The page reads `useParams`, so it has to be mounted through a real <Routes>
  // tree — rendering the element directly would silently yield no param.
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/:slug" element={<FormatRoutePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderAt(path: string, element: ReactElement) {
  return renderToString(<MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>);
}

describe("FormatRoutePage", () => {
  it.each([
    "/pdf-converter",
    "/tar.bz2-converter",
    "/notarealformat-converter",
    "/pdf-to-docx",
    "/tar.bz2-to-zip",
    "/pdf-to-notreal",
    "/not a slug",
    "/pdf-to-b-to-c",
    `/${"a".repeat(60)}-converter`,
  ])("renders %o without throwing", (path) => {
    expect(renderRoute(path).length).toBeGreaterThan(0);
  });

  it("renders a real 404 panel rather than an empty shell", () => {
    const html = renderRoute("/pricing");
    expect(html).toContain("Page not found");
    expect(html).toContain("/convert");
  });

  it("renders a conversion page for a valid pair", () => {
    // Effects don't run, so this asserts the loading branch, not the graph.
    expect(renderRoute("/pdf-to-docx")).toContain("Loading");
  });
});

describe("LandingPage", () => {
  it("renders the hero, the converter card and the format catalogue anchor", () => {
    const html = renderAt("/", <LandingPage />);
    expect(html).toContain("Convert anything.");
    expect(html).toContain('id="format-catalog"');
    // The converter card exposes the source → target pair as one labelled group.
    expect(html).toContain('role="group"');
  });
});
