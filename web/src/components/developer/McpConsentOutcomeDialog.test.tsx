// Render tests for the consent handoff dialog.
//
// The repo has no jsdom, so `renderToString` is the renderer and a click cannot
// be fired. What this can still pin is the part that matters to a visitor who is
// about to be sent off-origin: that the dialog names the application, states the
// outcome, and offers a control that returns to it. The handoff itself
// (`window.location.assign`) is wired in `AuthorizePage` and exercised by the
// browser walkthrough.

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { McpConsentOutcomeDialog } from "@/components/developer/McpConsentOutcomeDialog";
import type { ConsentOutcome } from "@/lib/mcpConsent";

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

const APPROVED: ConsentOutcome = {
  kind: "approved",
  clientName: "Claude Desktop",
  redirectUrl: "http://127.0.0.1:53422/callback",
};

function render(outcome: ConsentOutcome): string {
  return renderToString(
    <McpConsentOutcomeDialog outcome={outcome} onReturn={() => {}} onManage={() => {}} />,
  );
}

/**
 * The markup with React's SSR text separator removed.
 *
 * `renderToString` splits adjacent static text and an interpolated value with
 * `<!-- -->`, so `Return to {name}` renders as `Return to <!-- -->Claude
 * Desktop`. The comment is not part of the visible label, so assertions are made
 * against the text a user would read.
 */
function visibleText(html: string): string {
  return html.replace(/<!--.*?-->/g, "");
}

describe("McpConsentOutcomeDialog", () => {
  it("names the application and offers to return to it", () => {
    const html = visibleText(render(APPROVED));
    expect(html).toContain("Claude Desktop is connected");
    expect(html).toContain("Return to Claude Desktop");
  });

  it("says the connection is already saved", () => {
    // The visitor is about to leave our origin for a loopback port that may no
    // longer be listening. Without this sentence a failed handoff reads as a
    // failed connection, and they retry a grant that already exists.
    expect(render(APPROVED)).toContain("already saved to your account");
  });

  it("reports clearly that nothing was shared when refused", () => {
    const html = visibleText(render({ ...APPROVED, kind: "denied" }));
    expect(html).toContain("Connection cancelled");
    expect(html).toContain("Nothing on your account was shared");
    expect(html).not.toContain("is connected");
  });

  it("never renders the redirect destination", () => {
    // The destination is the application's own callback. Printing it (or, worse,
    // an authorization code) turns a consent screen into a way to leak a
    // credential the visitor cannot use and does not need to see.
    const html = render(APPROVED);
    expect(html).not.toContain("127.0.0.1");
    expect(html).not.toContain("code=");
  });
});
