// Tests for the download-URL scheme allowlist.
//
// An API-supplied URL used to be assigned straight to `a.href` with no scheme
// check. These cases pin the allowlist (https anywhere, http only on loopback
// for local dev) so a compromised or misconfigured API response cannot navigate
// the tab to an arbitrary scheme, while local development keeps working.

import { describe, expect, it } from "vitest";
import { isTrustedDownloadUrl } from "@/lib/download";

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
