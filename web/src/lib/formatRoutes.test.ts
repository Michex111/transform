import { describe, expect, it } from "vitest";
import { matchRoutes } from "react-router-dom";
import { parseFormatSlug } from "@/lib/formatRoutes";

describe("parseFormatSlug", () => {
  it("parses a format hub slug", () => {
    expect(parseFormatSlug("pdf-converter")).toEqual({ kind: "converter", ext: "pdf" });
  });

  it("parses a conversion slug", () => {
    expect(parseFormatSlug("pdf-to-docx")).toEqual({
      kind: "conversion",
      from: "pdf",
      to: "docx",
    });
  });

  it("keeps multi-dot extensions intact", () => {
    expect(parseFormatSlug("tar.bz2-converter")).toEqual({ kind: "converter", ext: "tar.bz2" });
    expect(parseFormatSlug("tar.bz2-to-zip")).toEqual({
      kind: "conversion",
      from: "tar.bz2",
      to: "zip",
    });
  });

  it("normalises case and digits", () => {
    expect(parseFormatSlug("PDF-To-DOCX")).toEqual({
      kind: "conversion",
      from: "pdf",
      to: "docx",
    });
    expect(parseFormatSlug("3gp-converter")).toEqual({ kind: "converter", ext: "3gp" });
    expect(parseFormatSlug("7z-to-zip")).toEqual({ kind: "conversion", from: "7z", to: "zip" });
  });

  it.each([
    "",
    "converter",
    "pdf",
    "pdf-to-",
    "-to-docx",
    "pdf-",
    "-pdf",
    "a-to-b-to-c",
    "pdf--converter",
    "verylongextensionname-converter",
    "pdf_converter",
    "pdf converter",
    "pdf/../secret-converter",
    "..pdf-converter",
    "pdf..docx-converter",
  ])("rejects the malformed slug %o", (slug) => {
    expect(parseFormatSlug(slug)).toEqual({ kind: "not-found" });
  });

  it("treats a missing slug as a 404", () => {
    expect(parseFormatSlug(undefined)).toEqual({ kind: "not-found" });
  });

  it("rejects absurdly long slugs", () => {
    expect(parseFormatSlug(`${"a".repeat(200)}-converter`)).toEqual({ kind: "not-found" });
  });

  // Even in the impossible case where a static path reached the dynamic route,
  // it must not be mistaken for a format page.
  it.each([
    "pricing",
    "security",
    "convert",
    "login",
    "register",
    "verify-email",
    "forgot-password",
    "reset-password",
  ])("does not treat the static path %o as a format", (slug) => {
    expect(parseFormatSlug(slug).kind).toBe("not-found");
  });
});

describe("public route ranking", () => {
  // Mirrors the public group in App.tsx. `/:slug` is deliberately listed FIRST
  // to prove the result comes from React Router's static-over-dynamic ranking,
  // not from declaration order.
  const routes = [
    { path: "/:slug" },
    { path: "/" },
    { path: "/pricing" },
    { path: "/security" },
    { path: "/convert" },
    { path: "/login" },
    { path: "/register" },
    // Reached by a hard load from the verification email, so a mis-ranking
    // here would break the sign-up flow for every user.
    { path: "/verify-email" },
    // Reached by a hard load from the password-reset email (and its recovery
    // link). A mis-ranking here renders a format page instead of the reset
    // form, and the emailed link silently does nothing.
    { path: "/forgot-password" },
    { path: "/reset-password" },
  ];

  // `Array.prototype.at` is not in the configured ES2020 lib, so index manually.
  const lastMatchedPath = (path: string): string | undefined => {
    const matches = matchRoutes(routes, path);
    return matches?.[matches.length - 1]?.route.path;
  };

  it.each([
    "/pricing",
    "/security",
    "/convert",
    "/login",
    "/register",
    "/verify-email",
    "/forgot-password",
    "/reset-password",
  ])("resolves %s to its own static route, not /:slug", (path) => {
    expect(lastMatchedPath(path)).toBe(path);
  });

  it("still resolves format paths to /:slug", () => {
    expect(lastMatchedPath("/pdf-converter")).toBe("/:slug");
    expect(lastMatchedPath("/pdf-to-docx")).toBe("/:slug");
  });

  it("does not match a nested path with /:slug", () => {
    expect(matchRoutes(routes, "/app/dashboard")).toBeNull();
  });
});
