// Tests for the assistant-artifact → drive rules.
//
// The failure modes here are quiet ones: a root-level file whose "Go to folder"
// points at a fabricated empty id, an id with a `&` that forges a second query
// parameter, or a thumbnail that falls back to the file-name tail when the
// server already told us the real extension.

import { describe, expect, it } from "vitest";
import type { AssistantArtifact } from "@/api/types";
import {
  artifactFolderId,
  driveFolderHref,
  fileArtifactExtension,
  folderHrefForArtifact,
} from "@/lib/fileArtifact";

function file(overrides: Partial<AssistantArtifact> = {}): AssistantArtifact {
  return { type: "file", id: "f-1", name: "report.pdf", meta: null, ...overrides };
}

describe("driveFolderHref", () => {
  it("deep-links a real folder id", () => {
    expect(driveFolderHref("f-123")).toBe("/app/files?folder=f-123");
  });

  it("links the drive root for every unset shape", () => {
    // A root-level file's `folder_id` is null; an older artifact may not carry
    // the key at all. Neither is an error and both must land somewhere real.
    expect(driveFolderHref(null)).toBe("/app/files");
    expect(driveFolderHref(undefined)).toBe("/app/files");
    expect(driveFolderHref("")).toBe("/app/files");
    expect(driveFolderHref("   ")).toBe("/app/files");
    expect(driveFolderHref(7 as never)).toBe("/app/files");
  });

  it("trims an id and encodes the query value", () => {
    expect(driveFolderHref("  f-123  ")).toBe("/app/files?folder=f-123");
    // An unencoded `&` would forge a second query parameter.
    expect(driveFolderHref("a&b")).toBe("/app/files?folder=a%26b");
    expect(driveFolderHref("a b/c")).toBe("/app/files?folder=a%20b%2Fc");
  });
});

describe("artifactFolderId", () => {
  it("reads the id out of meta", () => {
    expect(artifactFolderId(file({ meta: { folder_id: "f-9" } }))).toBe("f-9");
  });

  it("folds every unusable shape into the root", () => {
    expect(artifactFolderId(file({ meta: null }))).toBeNull();
    expect(artifactFolderId(file({ meta: {} }))).toBeNull();
    expect(artifactFolderId(file({ meta: { folder_id: null } }))).toBeNull();
    expect(artifactFolderId(file({ meta: { folder_id: "" } }))).toBeNull();
    expect(artifactFolderId(file({ meta: { folder_id: "  " } }))).toBeNull();
    expect(artifactFolderId(file({ meta: { folder_id: 7 } }))).toBeNull();
  });
});

describe("folderHrefForArtifact", () => {
  it("links the folder artifact's own id", () => {
    expect(
      folderHrefForArtifact({ type: "folder", id: "fold-1", name: "Invoices" }),
    ).toBe("/app/files?folder=fold-1");
  });

  it("falls back to the root for an empty id", () => {
    expect(folderHrefForArtifact({ type: "folder", id: "", name: "Invoices" })).toBe(
      "/app/files",
    );
  });
});

describe("fileArtifactExtension", () => {
  it("prefers the server's meta.extension", () => {
    expect(fileArtifactExtension(file({ name: "no-dot-name", meta: { extension: "pdf" } }))).toBe(
      "pdf",
    );
  });

  it("normalises a dotted, uppercase meta extension", () => {
    expect(fileArtifactExtension(file({ meta: { extension: ".DOCX" } }))).toBe("docx");
  });

  it("derives from the name when meta is absent or unusable", () => {
    expect(fileArtifactExtension(file({ name: "Report.PDF", meta: null }))).toBe("pdf");
    expect(fileArtifactExtension(file({ name: "Report.PDF", meta: {} }))).toBe("pdf");
    expect(fileArtifactExtension(file({ name: "Report.PDF", meta: { extension: 7 } }))).toBe("pdf");
    expect(fileArtifactExtension(file({ name: "Report.PDF", meta: { extension: "  " } }))).toBe(
      "pdf",
    );
  });

  it("returns an empty string only when there is nothing to derive", () => {
    // `FormatThumb` renders "" as its neutral FILE tile, so this must not
    // invent an extension the server never claimed.
    expect(fileArtifactExtension(file({ name: "", meta: null }))).toBe("");
  });
});
