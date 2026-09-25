// Tests for the "Save to Drive" rules.
//
// The failure modes here are all quiet ones: treating a missing preference as
// an error (or as a folder named "undefined"), offering to save a job that has
// no output, or telling the user their file is "saved" while it is still only
// queued — and, worst of the set, moving a file to the root without saying so
// when the configured folder is gone.

import { describe, expect, it } from "vitest";
import {
  ROOT_FOLDER_LABEL,
  canSaveToDrive,
  defaultSaveFolderId,
  folderLabel,
  saveQueuedMessage,
} from "@/lib/saveToDrive";

describe("folderLabel", () => {
  it("calls the root My Drive", () => {
    expect(folderLabel(null)).toBe("My Drive");
    expect(folderLabel(undefined)).toBe("My Drive");
    expect(ROOT_FOLDER_LABEL).toBe(folderLabel(null));
  });

  it("uses a named folder's own name", () => {
    expect(folderLabel({ name: "Invoices" })).toBe("Invoices");
  });

  it("degrades a blank name to the root instead of an empty label", () => {
    // A malformed payload reaches this: the label is interpolated into toast
    // copy, where "" would read as "Saving to  in the background".
    expect(folderLabel({ name: "" })).toBe("My Drive");
    expect(folderLabel({ name: "   " })).toBe("My Drive");
  });
});

describe("defaultSaveFolderId", () => {
  it("returns a configured folder id", () => {
    expect(defaultSaveFolderId({ default_save_folder_id: "f-123" })).toBe("f-123");
  });

  it("treats every unset shape as 'no preference'", () => {
    // The field is optional on the wire (independent deploys), so an older API
    // that omits it, an explicit null, the API's cleared value, and a
    // whitespace-only string all mean the same thing: save to the root.
    expect(defaultSaveFolderId({})).toBeNull();
    expect(defaultSaveFolderId({ default_save_folder_id: null })).toBeNull();
    expect(defaultSaveFolderId({ default_save_folder_id: undefined })).toBeNull();
    expect(defaultSaveFolderId({ default_save_folder_id: "" })).toBeNull();
    expect(defaultSaveFolderId({ default_save_folder_id: "   " })).toBeNull();
  });

  it("rejects a non-string from a malformed payload", () => {
    // A number from a proxy or a stale cache is not an id, and passing it to
    // `getFolderContents` would build `.../folders/7` and 404 the save.
    expect(defaultSaveFolderId({ default_save_folder_id: 7 } as never)).toBeNull();
    expect(defaultSaveFolderId({ default_save_folder_id: {} } as never)).toBeNull();
  });

  it("tolerates a missing user", () => {
    expect(defaultSaveFolderId(null)).toBeNull();
    expect(defaultSaveFolderId(undefined)).toBeNull();
  });

  it("trims a padded id, which no real id contains", () => {
    expect(defaultSaveFolderId({ default_save_folder_id: " f-123 " })).toBe("f-123");
  });
});

describe("canSaveToDrive", () => {
  it("accepts only the terminal completed state", () => {
    expect(canSaveToDrive({ status: "COMPLETED" })).toBe(true);
  });

  it("refuses everything that has no output yet", () => {
    for (const status of ["PENDING", "PROCESSING", "AWAITING_UPLOAD", "FAILED", "CANCELLED"]) {
      expect(canSaveToDrive({ status })).toBe(false);
    }
  });

  it("does not accept a second spelling of completed", () => {
    // The app has exactly one spelling; accepting `"completed"` here would hide
    // a status that came from somewhere else entirely.
    expect(canSaveToDrive({ status: "completed" })).toBe(false);
    expect(canSaveToDrive({ status: "DONE" })).toBe(false);
  });
});

describe("saveQueuedMessage", () => {
  it("names the destination and admits the transfer is only queued", () => {
    const message = saveQueuedMessage({ name: "Invoices" });
    expect(message).toContain("Invoices");
    expect(message).toContain("uploads dock");
    // Never claim it is done: the dock may still report a failure.
    expect(message).not.toContain("Saved");
    expect(message).not.toContain("saved");
  });

  it("names the root when there is no folder", () => {
    const message = saveQueuedMessage(null);
    expect(message).toContain("My Drive");
    expect(message).not.toContain("Saved");
  });

  it("says so when the file was moved to the root", () => {
    const message = saveQueuedMessage(null, { fellBackToRoot: true });
    expect(message).toContain("My Drive");
    expect(message).toContain("default folder");
    expect(message).not.toContain("Saved");
  });

  it("does not claim a fallback when there was none", () => {
    // The unset preference is the normal case: saving to the root is the
    // intended destination, not a recovery from a broken one.
    expect(saveQueuedMessage(null).toLowerCase()).not.toContain("unavailable");
    expect(saveQueuedMessage(null).toLowerCase()).not.toContain("no longer");
  });

  it("keeps a destination label out of the sentence only when it is blank", () => {
    expect(saveQueuedMessage({ name: "" })).toBe(saveQueuedMessage(null));
  });
});
