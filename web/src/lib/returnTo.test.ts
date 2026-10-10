import { describe, expect, it } from "vitest";
import {
  DEFAULT_RETURN_PATH,
  MCP_AUTHORIZE_ROUTE,
  isAuthorizationRequest,
  safeReturnPath,
} from "@/lib/returnTo";

describe("safeReturnPath", () => {
  it("keeps an in-app path, query string included", () => {
    // Both of these are real recorded destinations, and dropping the query
    // string is the failure being guarded against in each case: the consent
    // route loses the whole authorization request, and the billing return loses
    // the payment confirmation.
    expect(safeReturnPath("/app/authorize?client_id=x&scope=a+b")).toBe(
      "/app/authorize?client_id=x&scope=a+b",
    );
    expect(safeReturnPath("/app/billing?credits=success")).toBe("/app/billing?credits=success");
  });

  it("falls back when nothing usable was recorded", () => {
    expect(safeReturnPath(undefined)).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath(null)).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath(42)).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath({ from: "/app/dashboard" })).toBe(DEFAULT_RETURN_PATH);
  });

  it("refuses anything that would leave the origin", () => {
    // The result is used as a navigation target, so an absolute URL here turns
    // our own sign-in into an open redirect: the visitor is handed to a page
    // that can imitate the one they just left. `//host` and `/\host` are both
    // read as a full URL to another origin by the browser.
    expect(safeReturnPath("https://evil.example/login")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("//evil.example/login")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("/\\evil.example")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("javascript:alert(1)")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("app/dashboard")).toBe(DEFAULT_RETURN_PATH);
  });

  it("honours a caller-supplied fallback", () => {
    expect(safeReturnPath("https://evil.example", "/app/billing")).toBe("/app/billing");
  });
});

describe("isAuthorizationRequest", () => {
  it("recognises the consent route with and without a query string", () => {
    expect(isAuthorizationRequest(MCP_AUTHORIZE_ROUTE)).toBe(true);
    expect(isAuthorizationRequest(`${MCP_AUTHORIZE_ROUTE}?client_id=x`)).toBe(true);
  });

  it("does not match other destinations", () => {
    expect(isAuthorizationRequest("/app/dashboard")).toBe(false);
    expect(isAuthorizationRequest("/app/billing?credits=success")).toBe(false);
    expect(isAuthorizationRequest(`${MCP_AUTHORIZE_ROUTE}-extra`)).toBe(false);
  });
});

describe("MCP_AUTHORIZE_ROUTE", () => {
  it("matches the path the API redirects the browser to", () => {
    // The API builds its consent URL from `MCP_CONSENT_PATH` in
    // `src/infrastructure/config/settings.py` (default "/app/authorize"); the
    // router and the guards use this constant. Renaming one side and not the
    // other sends every connecting harness to a 404, so the literal is pinned.
    expect(MCP_AUTHORIZE_ROUTE).toBe("/app/authorize");
  });
});
