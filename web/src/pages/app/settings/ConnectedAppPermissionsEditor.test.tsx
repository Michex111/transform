// Render tests for the in-place permissions editor.
//
// The repo has no jsdom, so `renderToString` is the renderer and a click cannot
// be fired. What this pins is the copy and the rules a user depends on before
// saving: that all four permissions and both confinement choices are visible in
// the product's own words, that a newly added delete needs an explicit
// confirmation, and that Save is blocked with an explanation when the selection
// is incomplete.
//
// The editor is fully controlled, which is why it can be rendered with any
// selection here — including one carrying a new delete grant that the default
// seed would never produce. `lib/connectedAppPermissions.test.ts` covers the
// pure payload and effect-wording rules this markup is wired to.

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ConnectedAppPermissionsEditor } from "@/pages/app/settings/ConnectedAppPermissionsEditor";
import type { ConnectedAppResponse, McpFolderOption } from "@/api/types";
import {
  DESTRUCTIVE_SCOPE,
  initialPermissionState,
  type ConnectedAppPermissionState,
} from "@/lib/connectedAppPermissions";

// Rendering an effectful tree (motion's Button uses a layout effect) through
// `renderToString` logs a benign "does nothing on the server" warning.
// Silenced so the suite output stays readable; anything else still surfaces.
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

const APP: ConnectedAppResponse = {
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
};

const FOLDERS: McpFolderOption[] = [
  { folder_id: "f1", name: "Reports" },
  { folder_id: "f2", name: "Invoices" },
];

function render(app: ConnectedAppResponse, state: ConnectedAppPermissionState): string {
  return renderToString(
    <ConnectedAppPermissionsEditor
      app={app}
      state={state}
      onChange={() => {}}
      folders={FOLDERS}
      saving={false}
      onSave={() => {}}
      onClose={() => {}}
    />,
  );
}

/** The markup with React's SSR text separator removed (see RegisterPage.test). */
function visibleText(html: string): string {
  return html.replace(/<!--.*?-->/g, "");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** One rendered `<input>`'s tag, matched on its element id. */
function inputById(html: string, id: string): string {
  return html.match(new RegExp(`<input[^>]*id="${escapeRegExp(id)}"[^>]*>`))?.[0] ?? "";
}

/** The save button's tag, so its disabled state can be asserted. */
function saveButton(html: string): string {
  return html.match(/<button[^>]*>Save changes<\/button>/)?.[0] ?? "";
}

/**
 * Whether the save button actually carries a `disabled` attribute.
 *
 * A plain `toContain("disabled")` would match the `disabled:` variant classes in
 * its className, so this looks for the attribute (React renders `disabled=""`).
 */
function saveDisabled(html: string): boolean {
  return /\sdisabled(?:="[^"]*")?(?=[\s>])/.test(saveButton(html));
}

describe("ConnectedAppPermissionsEditor permissions", () => {
  it("offers all four permissions in the product's wording", () => {
    const html = visibleText(render(APP, initialPermissionState(APP)));
    expect(html).toContain("Read files");
    expect(html).toContain("Convert");
    expect(html).toContain("Save files");
    expect(html).toContain("Delete files");
    for (const scope of ["documents.read", "documents.convert", "documents.write", "documents.delete"]) {
      expect(inputById(html, `edit-scope-${scope}`)).not.toBe("");
    }
  });

  it("pre-selects the permissions already granted", () => {
    const html = render(APP, initialPermissionState(APP));
    expect(inputById(html, "edit-scope-documents.read")).toContain("checked");
    expect(inputById(html, "edit-scope-documents.convert")).toContain("checked");
    expect(inputById(html, "edit-scope-documents.write")).not.toContain("checked");
    expect(inputById(html, "edit-scope-documents.delete")).not.toContain("checked");
  });

  it("offers both folder and history options in the consent screen's wording", () => {
    const html = visibleText(render(APP, initialPermissionState(APP)));
    expect(html).toContain("All folders in your Drive");
    expect(html).toContain("Only one folder you choose");
    expect(html).toContain("Only conversions it started");
    expect(html).toContain("Your entire conversion history");
  });

  it("does not offer to create a folder (the consent screen owns that)", () => {
    const html = visibleText(
      render(APP, initialPermissionState({ ...APP, folder_access: "FOLDER", folder_id: "f1" })),
    );
    expect(html).not.toContain("Create a new folder for this app");
    // But it does let the user pick an existing one.
    expect(html).toContain("Reports");
  });
});

describe("ConnectedAppPermissionsEditor destructive guard", () => {
  it("requires an explicit confirmation for a newly added delete", () => {
    const html = visibleText(
      render(APP, { ...initialPermissionState(APP), scopes: ["documents.read", DESTRUCTIVE_SCOPE] }),
    );
    expect(html).toContain("Let this app delete your files");
    expect(saveDisabled(html)).toBe(true);
  });

  it("allows saving once a newly added delete is confirmed", () => {
    const html = visibleText(
      render(APP, {
        ...initialPermissionState(APP),
        scopes: ["documents.read", DESTRUCTIVE_SCOPE],
        confirmDestructive: true,
      }),
    );
    expect(html).toContain("Let this app delete your files");
    expect(saveDisabled(html)).toBe(false);
  });

  it("does not ask for a confirmation when delete was already granted", () => {
    const app: ConnectedAppResponse = { ...APP, scopes: ["documents.read", DESTRUCTIVE_SCOPE] };
    const html = visibleText(render(app, initialPermissionState(app)));
    expect(html).not.toContain("Let this app delete your files");
  });
});

describe("ConnectedAppPermissionsEditor validation", () => {
  it("blocks saving and explains when no permission is ticked", () => {
    const html = visibleText(render(APP, { ...initialPermissionState(APP), scopes: [] }));
    expect(saveDisabled(html)).toBe(true);
    expect(html).toContain("Choose at least one permission");
  });

  it("blocks saving and explains when 'one folder' has nothing chosen", () => {
    const html = visibleText(
      render(APP, { ...initialPermissionState(APP), folderAccess: "FOLDER", folderId: null }),
    );
    expect(saveDisabled(html)).toBe(true);
    expect(html).toContain("Choose which folder this app may use");
  });
});

describe("ConnectedAppPermissionsEditor effect messaging", () => {
  it("says a reduction takes effect on the app's next request", () => {
    const html = visibleText(
      render(APP, { ...initialPermissionState(APP), scopes: ["documents.read"] }),
    );
    expect(html).toContain("next request");
    expect(html).toContain("re-authorize");
  });

  it("says an addition waits for re-authorization rather than being instant", () => {
    const html = visibleText(
      render(APP, {
        ...initialPermissionState(APP),
        scopes: ["documents.read", "documents.convert", "documents.write"],
      }),
    );
    expect(html).toContain("does not start working");
    expect(html).toContain("re-authorizes");
  });
});
