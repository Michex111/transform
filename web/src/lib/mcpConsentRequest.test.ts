// Tests for the consent screen's confinement rules.
//
// These are the decisions a user cannot see fail: an agent silently left with
// whole-Drive access, or a body the API rejects with a 400 the screen could
// have prevented. They are pure so they can be asserted directly, without a
// renderer (this project has no jsdom).

import { describe, expect, it } from "vitest";
import {
  FOLDER_ACCESS_LABELS,
  HISTORY_SCOPE_LABELS,
  buildConsentConfinement,
  buildFolderAccessPayload,
  describeFolderChoice,
  effectiveHistoryScope,
  initialFolderChoice,
  initialHistoryScope,
  validateFolderChoice,
  type FolderChoice,
} from "@/lib/mcpConsentRequest";

describe("initialFolderChoice", () => {
  it("keeps a confined binding rather than widening it to the whole Drive", () => {
    // The important case: re-consenting an agent already limited to a folder
    // must not silently re-offer it everything.
    const choice = initialFolderChoice({ folder_access: "FOLDER", folder_id: "f1" });
    expect(choice.folderAccess).toBe("FOLDER");
    expect(choice.folderId).toBe("f1");
  });

  it("stays on FOLDER even without a bound folder, so nothing is widened by default", () => {
    const choice = initialFolderChoice({ folder_access: "FOLDER", folder_id: null });
    expect(choice.folderAccess).toBe("FOLDER");
    expect(choice.folderId).toBeNull();
    // And that incomplete choice is not submittable — the user must pick.
    expect(validateFolderChoice(choice).ok).toBe(false);
  });

  it("keeps a whole-Drive binding as ALL", () => {
    expect(initialFolderChoice({ folder_access: "ALL", folder_id: null }).folderAccess).toBe("ALL");
  });
});

describe("initialHistoryScope", () => {
  it("defaults to the least an agent can read, never ALL", () => {
    expect(initialHistoryScope()).toBe("AGENT");
  });
});

describe("effectiveHistoryScope", () => {
  it("honours the user's choice when the API allows one", () => {
    expect(
      effectiveHistoryScope({ history_scope: "AGENT", can_choose_history_scope: true }, "ALL"),
    ).toBe("ALL");
  });

  it("submits the API's fixed value when there is nothing to choose", () => {
    expect(
      effectiveHistoryScope({ history_scope: "ALL", can_choose_history_scope: false }, "AGENT"),
    ).toBe("ALL");
    expect(
      effectiveHistoryScope({ history_scope: "AGENT", can_choose_history_scope: false }, "ALL"),
    ).toBe("AGENT");
  });
});

describe("validateFolderChoice", () => {
  it("accepts the whole-Drive choice", () => {
    expect(validateFolderChoice({ folderAccess: "ALL", folderId: null, newFolderName: "" }).ok).toBe(
      true,
    );
  });

  it("accepts a picked folder", () => {
    expect(
      validateFolderChoice({ folderAccess: "FOLDER", folderId: "f1", newFolderName: "" }).ok,
    ).toBe(true);
  });

  it("accepts a new folder with a non-blank name", () => {
    expect(
      validateFolderChoice({ folderAccess: "FOLDER", folderId: null, newFolderName: "  Notes  " })
        .ok,
    ).toBe(true);
  });

  it("rejects 'one folder' with neither a folder nor a name, and says why", () => {
    const result = validateFolderChoice({
      folderAccess: "FOLDER",
      folderId: null,
      newFolderName: "   ",
    });
    expect(result.ok).toBe(false);
    expect(result.message).toBeTruthy();
  });
});

describe("buildFolderAccessPayload", () => {
  it("sends ALL with no folder fields", () => {
    expect(
      buildFolderAccessPayload({ folderAccess: "ALL", folderId: "f1", newFolderName: "x" }),
    ).toEqual({ folder_access: "ALL", folder_id: null, new_folder_name: null });
  });

  it("sends a picked folder as folder_id only", () => {
    expect(
      buildFolderAccessPayload({ folderAccess: "FOLDER", folderId: "f1", newFolderName: "" }),
    ).toEqual({ folder_access: "FOLDER", folder_id: "f1", new_folder_name: null });
  });

  it("sends a new folder as new_folder_name only, trimmed", () => {
    expect(
      buildFolderAccessPayload({ folderAccess: "FOLDER", folderId: null, newFolderName: "  Notes  " }),
    ).toEqual({ folder_access: "FOLDER", folder_id: null, new_folder_name: "Notes" });
  });

  it("never sends folder_id and new_folder_name together", () => {
    // Defensive: the UI clears one when the other is chosen, but a payload that
    // carried both would be ambiguous to the server.
    const payload = buildFolderAccessPayload({
      folderAccess: "FOLDER",
      folderId: "f1",
      newFolderName: "Notes",
    });
    expect(payload).not.toBeNull();
    expect(payload!.folder_id).toBe("f1");
    expect(payload!.new_folder_name).toBeNull();
  });

  it("returns null when 'one folder' is selected with nothing to bind", () => {
    expect(
      buildFolderAccessPayload({ folderAccess: "FOLDER", folderId: null, newFolderName: "" }),
    ).toBeNull();
  });
});

describe("buildConsentConfinement", () => {
  it("refuses to build an incomplete body", () => {
    expect(
      buildConsentConfinement({
        folderChoice: { folderAccess: "FOLDER", folderId: null, newFolderName: "" },
        historyScope: "AGENT",
      }),
    ).toBeNull();
  });

  it("carries the folder and history choices together", () => {
    expect(
      buildConsentConfinement({
        folderChoice: { folderAccess: "FOLDER", folderId: "f1", newFolderName: "" },
        historyScope: "ALL",
      }),
    ).toEqual({
      folder_access: "FOLDER",
      folder_id: "f1",
      new_folder_name: null,
      history_scope: "ALL",
    });
  });
});

describe("describeFolderChoice", () => {
  it("states plainly what a whole-Drive grant reaches", () => {
    expect(
      describeFolderChoice({
        folderChoice: { folderAccess: "ALL", folderId: null, newFolderName: "" },
      }),
    ).toContain("every folder in your Drive");
  });

  it("names the confined folder when it can be resolved", () => {
    const choice: FolderChoice = { folderAccess: "FOLDER", folderId: "f1", newFolderName: "" };
    expect(describeFolderChoice({ folderChoice: choice, folderName: "Reports" })).toContain(
      "Reports",
    );
  });

  it("explains a folder that is about to be created", () => {
    const choice: FolderChoice = { folderAccess: "FOLDER", folderId: null, newFolderName: "Notes" };
    expect(describeFolderChoice({ folderChoice: choice })).toContain("Notes");
  });

  it("prompts for a choice when none is made", () => {
    const choice: FolderChoice = { folderAccess: "FOLDER", folderId: null, newFolderName: "" };
    expect(describeFolderChoice({ folderChoice: choice })).toContain("Choose a folder");
  });
});

describe("option copy", () => {
  it("uses the product's wording for both choices", () => {
    // Pinned so the screen cannot drift from the backend's own descriptions.
    expect(FOLDER_ACCESS_LABELS.ALL).toBe("All folders in your Drive");
    expect(FOLDER_ACCESS_LABELS.FOLDER).toBe("Only one folder you choose");
    expect(HISTORY_SCOPE_LABELS.AGENT).toBe("Only conversions it started");
    expect(HISTORY_SCOPE_LABELS.ALL).toBe("Your entire conversion history");
  });
});
