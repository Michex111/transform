import { describe, expect, it } from "vitest";
import { denialUrl } from "@/lib/mcpConsent";

describe("denialUrl", () => {
  it("adds the standard refusal and echoes the state", () => {
    const parsed = new URL(denialUrl("http://127.0.0.1:53422/callback", "abc123")!);
    expect(parsed.searchParams.get("error")).toBe("access_denied");
    expect(parsed.searchParams.get("state")).toBe("abc123");
  });

  it("omits state when the request carried none", () => {
    const parsed = new URL(denialUrl("http://127.0.0.1:53422/callback", null)!);
    expect(parsed.searchParams.has("state")).toBe(false);
  });

  it("preserves a query string already on the callback", () => {
    // The registered redirect URI can legitimately carry its own parameters, and
    // rebuilding it instead of editing it would drop them.
    const parsed = new URL(denialUrl("https://app.example/cb?tenant=acme", "s")!);
    expect(parsed.searchParams.get("tenant")).toBe("acme");
    expect(parsed.searchParams.get("error")).toBe("access_denied");
  });

  it("returns null for an unparseable URI so the caller can stop", () => {
    // Not theoretical: the value comes from the client's registration, and the
    // caller's job is to send the visitor somewhere useful rather than navigate
    // to something unusable.
    expect(denialUrl("not a url", "s")).toBeNull();
  });
});
