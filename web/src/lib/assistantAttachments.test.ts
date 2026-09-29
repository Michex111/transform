// Tests for the attachment list rules: the cap, de-duplication by id, and the
// extension used for the thumbnail.

import { describe, expect, it } from "vitest";
import type { AssistantAttachment } from "@/api/types";
import {
  MAX_ASSISTANT_ATTACHMENTS,
  attachAssistantFile,
  attachmentExtension,
  attachmentFileIds,
  attachmentFromFile,
  attachmentLabel,
  canAttachMore,
  removeAssistantAttachment,
  withoutAttachmentIds,
} from "@/lib/assistantAttachments";

function attachment(overrides: Partial<AssistantAttachment> = {}): AssistantAttachment {
  return { id: "f1", name: "resume.pdf", extension: "pdf", ...overrides };
}

describe("attachmentExtension", () => {
  it("prefers the declared extension, normalising case and a leading dot", () => {
    expect(attachmentExtension(attachment({ extension: ".PDF" }))).toBe("pdf");
    expect(attachmentExtension(attachment({ extension: "docx" }))).toBe("docx");
  });

  it("derives the extension from the name when the server omitted it", () => {
    expect(attachmentExtension({ id: "f1", name: "Report.DOCX" })).toBe("docx");
  });

  it("falls back to txt for a nameless or dotless file", () => {
    expect(attachmentExtension({ id: "f1", name: "README" })).toBe("txt");
    expect(attachmentExtension({ id: "f1", name: "" })).toBe("txt");
    // A dotfile's leading dot is not an extension.
    expect(attachmentExtension({ id: "f1", name: ".env" })).toBe("txt");
  });
});

describe("attachmentLabel", () => {
  it("uses the name", () => {
    expect(attachmentLabel(attachment())).toBe("resume.pdf");
  });

  it("falls back to the id when the server echoed no name", () => {
    expect(attachmentLabel({ id: "f9", name: "  " })).toBe("f9");
  });
});

describe("attachmentFromFile", () => {
  it("derives the extension from the library file name", () => {
    expect(attachmentFromFile({ id: "f1", file_name: "notes.docx" })).toEqual({
      id: "f1",
      name: "notes.docx",
      extension: "docx",
    });
  });

  it("omits an empty extension", () => {
    const out = attachmentFromFile({ id: "f2", file_name: "LICENSE" });
    expect(out).toEqual({ id: "f2", name: "LICENSE" });
    expect("extension" in out).toBe(false);
  });
});

describe("attachAssistantFile", () => {
  it("appends a new file", () => {
    const result = attachAssistantFile([], attachment());
    expect(result.outcome).toBe("added");
    expect(result.attachments).toHaveLength(1);
  });

  it("is a no-op for a duplicate id, even with a different name", () => {
    const current = [attachment({ id: "f1", name: "old.pdf" })];
    const result = attachAssistantFile(current, attachment({ id: "f1", name: "new.pdf" }));
    expect(result.outcome).toBe("duplicate");
    expect(result.attachments).toEqual(current);
  });

  it("refuses to exceed the cap and says so", () => {
    const full = Array.from({ length: MAX_ASSISTANT_ATTACHMENTS }, (_, i) =>
      attachment({ id: `f${i}` }),
    );
    const result = attachAssistantFile(full, attachment({ id: "extra" }));
    expect(result.outcome).toBe("full");
    expect(result.attachments).toHaveLength(MAX_ASSISTANT_ATTACHMENTS);
    expect(canAttachMore(full)).toBe(false);
  });

  it("does not mutate the list it is given", () => {
    const current = [attachment()];
    const before = [...current];
    attachAssistantFile(current, attachment({ id: "f2" }));
    expect(current).toEqual(before);
  });

  it("honours a custom cap", () => {
    const result = attachAssistantFile([attachment()], attachment({ id: "f2" }), 1);
    expect(result.outcome).toBe("full");
  });
});

describe("canAttachMore", () => {
  it("defaults to the client cap when no plan cap is given", () => {
    const belowCap = Array.from({ length: MAX_ASSISTANT_ATTACHMENTS - 1 }, (_, i) =>
      attachment({ id: `f${i}` }),
    );
    expect(canAttachMore(belowCap)).toBe(true);
    expect(canAttachMore([...belowCap, attachment({ id: "last" })])).toBe(false);
  });

  it("uses the plan's cap when one is threaded through", () => {
    const one = [attachment({ id: "a" })];
    // A PRO_PLUS plan might allow 3, a free plan 1 — the number is the caller's.
    expect(canAttachMore(one, 3)).toBe(true);
    expect(canAttachMore(one, 1)).toBe(false);
    expect(canAttachMore([], 0)).toBe(false);
  });

  it("bounds attachAssistantFile by the same plan cap", () => {
    const current = [attachment({ id: "a" }), attachment({ id: "b" })];
    expect(attachAssistantFile(current, attachment({ id: "c" }), 3).outcome).toBe("added");
    expect(attachAssistantFile(current, attachment({ id: "c" }), 2).outcome).toBe("full");
  });
});

describe("removeAssistantAttachment / withoutAttachmentIds", () => {
  it("removes by id and ignores unknown ids", () => {
    const current = [attachment({ id: "a" }), attachment({ id: "b" })];
    expect(removeAssistantAttachment(current, "a").map((a) => a.id)).toEqual(["b"]);
    expect(removeAssistantAttachment(current, "zzz")).toHaveLength(2);
  });

  it("strips the files the server reported gone, keeping the rest", () => {
    const current = [attachment({ id: "a" }), attachment({ id: "b" }), attachment({ id: "c" })];
    expect(withoutAttachmentIds(current, ["b", "c"]).map((a) => a.id)).toEqual(["a"]);
  });
});

describe("attachmentFileIds", () => {
  it("returns the ids in order", () => {
    expect(attachmentFileIds([attachment({ id: "a" }), attachment({ id: "b" })])).toEqual([
      "a",
      "b",
    ]);
  });

  it("drops an id-less entry rather than sending an empty id", () => {
    expect(attachmentFileIds([attachment({ id: "" })])).toEqual([]);
  });
});
