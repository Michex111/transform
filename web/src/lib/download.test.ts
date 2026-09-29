// Tests for the download-URL scheme allowlist.
//
// An API-supplied URL used to be assigned straight to `a.href` with no scheme
// check. These cases pin the allowlist (https anywhere, http only on loopback
// for local dev) so a compromised or misconfigured API response cannot navigate
// the tab to an arbitrary scheme, while local development keeps working.

import { describe, expect, it } from "vitest";
import { isTrustedDownloadUrl, trustedExternalUrl } from "@/lib/download";

describe("isTrustedDownloadUrl", () => {
  it("accepts https everywhere", () => {
    expect(isTrustedDownloadUrl("https://bucket.example.com/key?X-Amz-Signature=abc")).toBe(true);
    expect(isTrustedDownloadUrl("https://transform-api.onrender.com/docs")).toBe(true);
  });

  it("accepts http only for the local dev loopback hosts", () => {
    expect(isTrustedDownloadUrl("http://localhost:9000/bucket/key")).toBe(true);
    expect(isTrustedDownloadUrl("http://127.0.0.1:9000/bucket/key")).toBe(true);
    expect(isTrustedDownloadUrl("http://[::1]:9000/bucket/key")).toBe(true);
  });

  it("rejects plain http on a public host", () => {
    expect(isTrustedDownloadUrl("http://bucket.example.com/key")).toBe(false);
    expect(isTrustedDownloadUrl("http://localhost.evil.com/key")).toBe(false);
  });

  it("rejects scripts, data URIs and other schemes", () => {
    expect(isTrustedDownloadUrl("javascript:alert(1)")).toBe(false);
    expect(isTrustedDownloadUrl("data:text/html;base64,PHNjcmlwdD4=")).toBe(false);
    expect(isTrustedDownloadUrl("file:///etc/passwd")).toBe(false);
    expect(isTrustedDownloadUrl("ftp://example.com/x")).toBe(false);
  });

  it("rejects a server-relative path (those go through the streaming endpoint)", () => {
    expect(isTrustedDownloadUrl("/api/v1/files/7/stream")).toBe(false);
  });
});

// `trustedExternalUrl` guards `window.location.assign` for API-supplied billing
// URLs (Stripe checkout / customer portal). Regression: those URLs used to be
// assigned verbatim, so a `javascript:` value in the response would have run in
// the app's origin, and any other host was an open redirect.
describe("trustedExternalUrl", () => {
  it("returns an https URL unchanged (the Stripe-hosted case)", () => {
    const url = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3";
    expect(trustedExternalUrl(url)).toBe(url);
  });

  it("returns a loopback http URL unchanged (local dev)", () => {
    const url = "http://localhost:4242/portal";
    expect(trustedExternalUrl(url)).toBe(url);
  });

  it("refuses a javascript: URL (the XSS this exists to stop)", () => {
    expect(trustedExternalUrl("javascript:alert(document.domain)")).toBeNull();
  });

  it("refuses data:, file:, and plain-http public hosts", () => {
    expect(trustedExternalUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(trustedExternalUrl("file:///etc/passwd")).toBeNull();
    expect(trustedExternalUrl("http://checkout.stripe.com.evil.example/c")).toBeNull();
  });

  it("refuses an empty or server-relative value", () => {
    expect(trustedExternalUrl("")).toBeNull();
    expect(trustedExternalUrl("/billing/portal")).toBeNull();
  });
});
