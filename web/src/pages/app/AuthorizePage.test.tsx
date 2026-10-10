// Render tests for the consent screen's folder + history choices.
//
// The repo has no jsdom, so `renderToString` is the renderer and a click cannot
// be fired. What this can still pin is the copy and the defaults a visitor
// depends on before approving: that both confinement choices are visible, that
// "create a new folder" is offered, that the wider history scope is not
// pre-selected, and that "one folder" with nothing chosen cannot be submitted.
//
// The screen is a separate component from `AuthorizePage` precisely so it can
// be rendered here with a request in hand — the page fetches in an effect, which
// does not run under `renderToString`. `lib/mcpConsentRequest.test.ts` covers
// the pure payload rules this markup is wired to.

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { McpConsentScreen } from "@/components/developer/McpConsentScreen";
import type { McpConsentRequestResponse } from "@/api/types";

// The page around this screen reads the auth and toast contexts and the API
// client; none contributes markup, and the real ones would only add state (or
// a network call) to the tree. Mocked the same way `LoginPage.test.tsx` does.
vi.mock("@/api/client", () => ({
  api: { mcpConsentRequest: async () => ({}) },
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

const BASE: McpConsentRequestResponse = {
  client_id: "c1",
  client_name: "Claude Desktop",
  redirect_uri: "http://127.0.0.1:53422/callback",
  resource: "https://api.test/mcp",
  scopes: [
    {
      scope: "documents.read",
      description: "Read your files",
      requested: true,
      already_granted: false,
      destructive: false,
    },
  ],
  folders: [
    { folder_id: "f1", name: "Reports" },
    { folder_id: "f2", name: "Invoices" },
  ],
  folder_access: "ALL",
  folder_id: null,
  history_scope: "AGENT",
  can_choose_history_scope: true,
};

function render(request: McpConsentRequestResponse): string {
  return renderToString(
    <McpConsentScreen
      request={request}
      selectedScopes={new Set(["documents.read"])}
      onToggleScope={() => {}}
      submitting={false}
      onApprove={() => {}}
      onDeny={() => {}}
    />,
  );
}

/** The markup with React's SSR text separator removed (see RegisterPage.test). */
function visibleText(html: string): string {
  return html.replace(/<!--.*?-->/g, "");
}

/** One rendered `<input>`'s tag, matched on the name/value this file controls. */
function inputTag(html: string, name: string, value: string): string {
  const re = new RegExp(`<input[^>]*name="${name}"[^>]*value="${value}"[^>]*>`);
  return html.match(re)?.[0] ?? "";
}

describe("McpConsentScreen folder choice", () => {
  it("shows both folder options in the product's wording", () => {
    const html = visibleText(render(BASE));
    expect(html).toContain("All folders in your Drive");
    expect(html).toContain("Only one folder you choose");
  });

  it("offers to create a new folder for the application", () => {
    // Revealed with the picker under "one folder", so switching to the
    // confined choice always has a usable affordance — including on an account
    // with no folders yet.
    const html = visibleText(render({ ...BASE, folder_access: "FOLDER", folder_id: null }));
    expect(html).toContain("Create a new folder for this app");
  });

  it("reveals the folder picker only for the confined choice", () => {
    // A whole-Drive request must not show a picker the user did not ask for.
    expect(render(BASE)).not.toContain("Folder the agent may use");
  });

  it("pre-selects a confined binding instead of resetting to the whole Drive", () => {
    const html = render({ ...BASE, folder_access: "FOLDER", folder_id: "f1" });
    expect(html).toContain("Folder the agent may use");
    expect(visibleText(html)).toContain("Reports");
  });

  it("disables approval and explains when 'one folder' has nothing chosen", () => {
    const html = visibleText(render({ ...BASE, folder_access: "FOLDER", folder_id: null }));
    const approve = html.match(/<button[^>]*>Allow access<\/button>/)?.[0] ?? "";
    expect(approve).toContain("disabled");
    expect(html).toContain("Choose a folder, or name a new one");
  });
});

describe("McpConsentScreen history choice", () => {
  it("offers both history scopes when the API allows a choice", () => {
    const html = visibleText(render(BASE));
    expect(html).toContain("Only conversions it started");
    expect(html).toContain("Your entire conversion history");
  });

  it("never pre-selects the entire-history scope", () => {
    const html = render(BASE);
    expect(inputTag(html, "mcp-history-scope", "AGENT")).toContain("checked");
    expect(inputTag(html, "mcp-history-scope", "ALL")).not.toContain("checked");
  });

  it("states the single allowed scope as text when there is nothing to choose", () => {
    const html = visibleText(render({ ...BASE, can_choose_history_scope: false }));
    expect(html).not.toContain('name="mcp-history-scope"');
    expect(html).toContain("Only conversions it started");
    expect(html).toContain("This is fixed for this application.");
  });
});
