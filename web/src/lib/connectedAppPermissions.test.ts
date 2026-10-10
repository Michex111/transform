// Tests for the in-place permission editor's rules.
//
// These are the decisions a user cannot see fail: a body the API rejects with a
// 400 the editor could have prevented, a `folder_id` left in the payload after
// switching back to the whole Drive (a silent widening), a `documents.delete`
// grant sent without the confirmation, and — the easy one to get wrong — the
// claim that a change takes effect instantly when an addition does not.
//
// Pure so they assert directly, without a renderer (this project has no jsdom).

import { describe, expect, it } from "vitest";
import type { ConnectedAppResponse } from "@/api/types";
import {
  DESTRUCTIVE_SCOPE,
  PERMISSION_NARROWING_MESSAGE,
  PERMISSION_WIDENING_MESSAGE,
  buildUpdateConnectedAppRequest,
  describeFolderConfinement,
  describeHistoryConfinement,
  describePermissionChange,
  describeUpdateError,
  initialPermissionState,
  requiresDestructiveConfirmation,
  toggleScope,
  validatePermissionEdit,
  validatePermissionSelection,
  type ConnectedAppPermissionState,
} from "@/lib/connectedAppPermissions";

/** An active connection with read + convert, on the whole Drive, agent history. */
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

function state(overrides: Partial<ConnectedAppPermissionState> = {}): ConnectedAppPermissionState {
  return {
    scopes: ["documents.read", "documents.convert"],
    folderAccess: "ALL",
    folderId: null,
    historyScope: "AGENT",
    confirmDestructive: false,
    ...overrides,
  };
}

describe("initialPermissionState", () => {
  it("pre-selects the connection's current values", () => {
    const seeded = initialPermissionState(
      app({
        scopes: ["documents.read", "documents.write"],
        folder_access: "FOLDER",
        folder_id: "f1",
        folder_name: "Reports",
        history_scope: "ALL",
      }),
    );
    expect(seeded.scopes).toEqual(["documents.read", "documents.write"]);
    expect(seeded.folderAccess).toBe("FOLDER");
    expect(seeded.folderId).toBe("f1");
    expect(seeded.historyScope).toBe("ALL");
  });

  it("never pre-confirms a destructive grant", () => {
    const seeded = initialPermissionState(app({ scopes: [DESTRUCTIVE_SCOPE] }));
    expect(seeded.confirmDestructive).toBe(false);
  });

  it("keeps a confined binding confined rather than widening it on open", () => {
    const seeded = initialPermissionState(app({ folder_access: "FOLDER", folder_id: "f1" }));
    expect(seeded.folderAccess).toBe("FOLDER");
  });
});

describe("toggleScope", () => {
  it("adds a missing scope and removes a present one", () => {
    expect(toggleScope(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleScope(["a", "b"], "a")).toEqual(["b"]);
  });

  it("does not mutate the input", () => {
    const scopes = ["a"];
    toggleScope(scopes, "b");
    expect(scopes).toEqual(["a"]);
  });
});

describe("validatePermissionSelection", () => {
  it("rejects an empty permission set", () => {
    const result = validatePermissionSelection(state({ scopes: [] }));
    expect(result.ok).toBe(false);
    expect(result.message).toBeTruthy();
  });

  it("rejects 'one folder' with nothing chosen", () => {
    const result = validatePermissionSelection(
      state({ folderAccess: "FOLDER", folderId: null }),
    );
    expect(result.ok).toBe(false);
    expect(result.message).toBeTruthy();
  });

  it("accepts a valid selection", () => {
    expect(validatePermissionSelection(state()).ok).toBe(true);
  });
});

describe("requiresDestructiveConfirmation", () => {
  it("is true only when delete is newly added", () => {
    expect(requiresDestructiveConfirmation(["documents.read"], ["documents.read", DESTRUCTIVE_SCOPE])).toBe(
      true,
    );
    // Already granted: no extra confirmation to re-affirm it.
    expect(requiresDestructiveConfirmation([DESTRUCTIVE_SCOPE], [DESTRUCTIVE_SCOPE])).toBe(false);
    // Removing it must never require a confirmation.
    expect(requiresDestructiveConfirmation([DESTRUCTIVE_SCOPE], ["documents.read"])).toBe(false);
  });
});

describe("validatePermissionEdit", () => {
  const original = app({ scopes: ["documents.read"] });

  it("blocks a newly added delete until it is confirmed", () => {
    const result = validatePermissionEdit({
      state: state({ scopes: ["documents.read", DESTRUCTIVE_SCOPE] }),
      original,
    });
    expect(result.ok).toBe(false);
    expect(result.message).toBeTruthy();
  });

  it("passes once a newly added delete is confirmed", () => {
    const result = validatePermissionEdit({
      state: state({ scopes: ["documents.read", DESTRUCTIVE_SCOPE], confirmDestructive: true }),
      original,
    });
    expect(result.ok).toBe(true);
  });

  it("does not require a confirmation to remove delete", () => {
    const result = validatePermissionEdit({
      state: state({ scopes: [] }),
      original: app({ scopes: [DESTRUCTIVE_SCOPE] }),
    });
    // Empty scopes is its own failure; the point is it is not the destructive one.
    expect(result.message).not.toContain("delete");
  });
});

describe("buildUpdateConnectedAppRequest", () => {
  it("nulls folder_id when the whole Drive is chosen, never widening silently", () => {
    // A stale folder id carried over from before the switch must not survive.
    const body = buildUpdateConnectedAppRequest({
      state: state({ folderAccess: "ALL", folderId: "f1" }),
      original: app(),
    });
    expect(body).not.toBeNull();
    expect(body!.folder_access).toBe("ALL");
    expect(body!.folder_id).toBeNull();
  });

  it("sends the chosen folder for a confined grant", () => {
    const body = buildUpdateConnectedAppRequest({
      state: state({ folderAccess: "FOLDER", folderId: "f1" }),
      original: app(),
    });
    expect(body).toEqual({
      scopes: ["documents.read", "documents.convert"],
      folder_access: "FOLDER",
      folder_id: "f1",
      history_scope: "AGENT",
      confirm_destructive: false,
    });
  });

  it("sets confirm_destructive only when delete is newly added", () => {
    const added = buildUpdateConnectedAppRequest({
      state: state({ scopes: ["documents.read", DESTRUCTIVE_SCOPE], confirmDestructive: true }),
      original: app({ scopes: ["documents.read"] }),
    });
    expect(added!.confirm_destructive).toBe(true);

    // Already granted: keeping it is not an "add", so the flag stays false.
    const kept = buildUpdateConnectedAppRequest({
      state: state({ scopes: ["documents.read", DESTRUCTIVE_SCOPE] }),
      original: app({ scopes: ["documents.read", DESTRUCTIVE_SCOPE] }),
    });
    expect(kept!.confirm_destructive).toBe(false);
  });

  it("does not send the body while the delete confirmation is outstanding", () => {
    const body = buildUpdateConnectedAppRequest({
      state: state({ scopes: ["documents.read", DESTRUCTIVE_SCOPE] }),
      original: app({ scopes: ["documents.read"] }),
    });
    expect(body).toBeNull();
  });

  it("does not send an invalid selection", () => {
    expect(
      buildUpdateConnectedAppRequest({ state: state({ scopes: [] }), original: app() }),
    ).toBeNull();
    expect(
      buildUpdateConnectedAppRequest({
        state: state({ folderAccess: "FOLDER", folderId: null }),
        original: app(),
      }),
    ).toBeNull();
  });
});

describe("describePermissionChange", () => {
  it("says nothing changed for an identical selection", () => {
    const effect = describePermissionChange({ state: state(), original: app() });
    expect(effect.direction).toBe("none");
    expect(effect.message).toBeNull();
  });

  it("warns that a reduction takes effect on the next request", () => {
    const effect = describePermissionChange({
      state: state({ scopes: ["documents.read"] }),
      original: app(),
    });
    expect(effect.direction).toBe("narrowing");
    expect(effect.message).toBe(PERMISSION_NARROWING_MESSAGE);
    expect(effect.message).toContain("next request");
  });

  it("says an addition waits for re-authorization rather than taking effect now", () => {
    const effect = describePermissionChange({
      state: state({ scopes: ["documents.read", "documents.convert", "documents.write"] }),
      original: app(),
    });
    expect(effect.direction).toBe("widening");
    expect(effect.message).toBe(PERMISSION_WIDENING_MESSAGE);
    // The dangerous mistake is implying the new permission works immediately.
    expect(effect.message).not.toContain("next request");
    expect(effect.message).toContain("re-authorizes");
  });

  it("treats a folder widening and narrowing by direction", () => {
    expect(
      describePermissionChange({
        state: state({ folderAccess: "FOLDER", folderId: "f1" }),
        original: app(),
      }).direction,
    ).toBe("narrowing");
    expect(
      describePermissionChange({
        state: state({ folderAccess: "ALL", folderId: null }),
        original: app({ folder_access: "FOLDER", folder_id: "f1" }),
      }).direction,
    ).toBe("widening");
  });

  it("treats a change of confined folder as narrowing", () => {
    const effect = describePermissionChange({
      state: state({ folderAccess: "FOLDER", folderId: "f2" }),
      original: app({ folder_access: "FOLDER", folder_id: "f1" }),
    });
    expect(effect.direction).toBe("narrowing");
  });

  it("treats history changes by direction", () => {
    expect(
      describePermissionChange({
        state: state({ historyScope: "AGENT" }),
        original: app({ history_scope: "ALL" }),
      }).direction,
    ).toBe("narrowing");
    expect(
      describePermissionChange({
        state: state({ historyScope: "ALL" }),
        original: app({ history_scope: "AGENT" }),
      }).direction,
    ).toBe("widening");
  });

  it("states both directions when an edit does both", () => {
    const effect = describePermissionChange({
      state: state({ scopes: ["documents.read", "documents.write"] }),
      original: app({ scopes: ["documents.read", "documents.convert"] }),
    });
    expect(effect.direction).toBe("both");
    expect(effect.message).toContain("next request");
    expect(effect.message).toContain("re-authorizes");
  });
});

describe("describeFolderConfinement", () => {
  it("names the whole Drive plainly", () => {
    expect(describeFolderConfinement({ folder_access: "ALL", folder_name: null })).toBe(
      "All folders",
    );
  });

  it("names the confined folder when it resolves", () => {
    expect(describeFolderConfinement({ folder_access: "FOLDER", folder_name: "Reports" })).toBe(
      "Folder: Reports",
    );
  });

  it("never renders a null folder name as text", () => {
    // A confined grant whose folder was deleted arrives as `folder_name: null`;
    // the row must say so, not print "null".
    const label = describeFolderConfinement({ folder_access: "FOLDER", folder_name: null });
    expect(label).toBe("Folder removed");
    expect(label).not.toContain("null");
  });
});

describe("describeHistoryConfinement", () => {
  it("reuses the pinned consent wording", () => {
    expect(describeHistoryConfinement("AGENT")).toBe("Only conversions it started");
    expect(describeHistoryConfinement("ALL")).toBe("Your entire conversion history");
  });
});

describe("describeUpdateError", () => {
  it("explains the two states the user can act on", () => {
    expect(describeUpdateError({ status: 409 })).toContain("already been revoked");
    expect(describeUpdateError({ status: 404 })).toContain("no longer connected");
  });

  it("prefers the API's own message for a validation failure", () => {
    expect(describeUpdateError({ status: 400, message: "folder_id is required" })).toBe(
      "folder_id is required",
    );
  });

  it("never returns an empty or raw message", () => {
    expect(describeUpdateError(new Error(""))).toBe("Could not update permissions. Please try again.");
    expect(describeUpdateError(undefined)).toBe("Could not update permissions. Please try again.");
  });
});
