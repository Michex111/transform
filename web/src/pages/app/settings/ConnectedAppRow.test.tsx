// Render tests for one AI-app row.
//
// The row is presentational, so it can be rendered directly with each status.
// What this pins is the rule a user relies on when a connection is revoked: a
// revoked row offers no editing (it has to be reconnected), and the row states
// the current confinement — including the honest "Folder removed" for a grant
// whose folder was deleted — without a second round trip through the editor.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ConnectedAppRow } from "@/pages/app/settings/ConnectedAppRow";
import type { ConnectedAppResponse } from "@/api/types";

function app(overrides: Partial<ConnectedAppResponse> = {}): ConnectedAppResponse {
  return {
    id: "g1",
    client_id: "c1",
    client_name: "Claude Desktop",
    scopes: ["documents.read", "documents.convert"],
    status: "ACTIVE",
    created_at: "2026-01-01T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
    folder_access: "ALL",
    folder_id: null,
    folder_name: null,
    history_scope: "AGENT",
    ...overrides,
  };
}

function render(value: ConnectedAppResponse): string {
  return renderToString(<ConnectedAppRow app={value} onEdit={() => {}} onRevoke={() => {}} />);
}

/** The markup with React's SSR text separator removed. */
function visibleText(html: string): string {
  return html.replace(/<!--.*?-->/g, "");
}

describe("ConnectedAppRow actions", () => {
  it("offers editing and revoking on an active row", () => {
    const html = render(app());
    expect(html).toContain('aria-label="Edit permissions for Claude Desktop"');
    expect(html).toContain('aria-label="Revoke access for Claude Desktop"');
  });

  it("offers no editing on a revoked row, and says it must be reconnected", () => {
    const html = visibleText(render(app({ status: "REVOKED", revoked_at: "2026-02-01T00:00:00Z" })));
    expect(html).not.toContain("Edit permissions");
    expect(html).not.toContain("Revoke access");
    expect(html).toContain("reconnect");
    expect(html).toContain("Revoked");
  });
});

describe("ConnectedAppRow confinement badges", () => {
  it("shows the whole-Drive confinement and history scope", () => {
    const html = visibleText(render(app()));
    expect(html).toContain("All folders");
    expect(html).toContain("Only conversions it started");
  });

  it("names a confined folder", () => {
    const html = visibleText(
      render(app({ folder_access: "FOLDER", folder_id: "f1", folder_name: "Reports" })),
    );
    expect(html).toContain("Folder: Reports");
    expect(html).not.toContain("All folders");
  });

  it("says a deleted folder was removed instead of rendering 'null'", () => {
    const html = visibleText(
      render(app({ folder_access: "FOLDER", folder_id: "f1", folder_name: null })),
    );
    expect(html).toContain("Folder removed");
    expect(html).not.toContain(">null<");
  });

  it("shows the wider history scope when it is granted", () => {
    const html = visibleText(render(app({ history_scope: "ALL" })));
    expect(html).toContain("Your entire conversion history");
  });
});
