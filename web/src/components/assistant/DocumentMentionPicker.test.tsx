// The `@` picker popup.
//
// The suite runs in the Node environment, so the component is rendered with
// `renderToString` (the same convention as `AttachmentChips.test.tsx`) and the
// produced HTML is asserted. What matters here is that every state is real and
// distinguishable — an empty list, a failed search and a pending one must never
// look like each other — and that the ARIA roles match the combobox pattern the
// composer's textarea depends on.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { FileMetadataResponse } from "@/api/types";
import { DocumentMentionPicker } from "@/components/assistant/DocumentMentionPicker";

function file(overrides: Partial<FileMetadataResponse> = {}): FileMetadataResponse {
  return {
    id: "f1",
    file_name: "upei residence.pdf",
    file_key: "upload/f1/upei residence.pdf",
    file_size_bytes: 594,
    mime_type: "application/pdf",
    folder_id: null,
    created_at: "2026-10-09T15:22:11.377880Z",
    expires_at: null,
    is_favorite: false,
    ...overrides,
  };
}

function render(overrides: Partial<Parameters<typeof DocumentMentionPicker>[0]> = {}) {
  return renderToString(
    <DocumentMentionPicker
      id="composer-documents"
      query=""
      results={[]}
      loading={false}
      error={null}
      activeIndex={0}
      onSelect={() => {}}
      onHover={() => {}}
      {...overrides}
    />,
  );
}

describe("DocumentMentionPicker", () => {
  it("prompts for a name when the mention is empty", () => {
    const html = render();
    expect(html).toContain("Start typing a file name");
    expect(html).toContain("Find a document");
  });

  it("names the query when a search returns nothing", () => {
    const html = render({ query: "zzz" });
    // The distinction matters: "no matches" and "nothing typed yet" are
    // different situations and must not read the same.
    expect(html).toContain("No files match");
    expect(html).toContain("zzz");
    expect(html).not.toContain("Start typing a file name");
  });

  it("announces a failed search instead of showing an empty list", () => {
    const html = render({ query: "res", error: "Could not search your files." });
    // `role="alert"` so the failure is announced, not just painted.
    expect(html).toContain('role="alert"');
    expect(html).toContain("Could not search your files.");
    // And it must not be dressed up as "no results".
    expect(html).not.toContain("No files match");
  });

  it("shows a live status while searching", () => {
    const html = render({ query: "res", loading: true });
    expect(html).toContain('role="status"');
    expect(html).toContain("Searching your files");
  });

  it("renders the results as a labelled listbox", () => {
    const html = render({ query: "upei", results: [file()] });
    expect(html).toContain('role="listbox"');
    expect(html).toContain('aria-label="Files in your Transform Drive"');
    expect(html).toContain('role="option"');
    expect(html).toContain("upei residence.pdf");
  });

  it("marks only the highlighted row as selected", () => {
    const html = render({
      query: "re",
      results: [file({ id: "a", file_name: "a.pdf" }), file({ id: "b", file_name: "b.pdf" })],
      activeIndex: 1,
    });
    const selected = html.match(/aria-selected="true"/g) ?? [];
    expect(selected).toHaveLength(1);
    // The second option is the selected one.
    expect(html).toMatch(/id="composer-documents-option-1"[^>]*aria-selected="true"/);
  });

  it("gives every option an id so aria-activedescendant can point at it", () => {
    const html = render({
      query: "re",
      results: [file({ id: "a" }), file({ id: "b" })],
    });
    expect(html).toContain('id="composer-documents-option-0"');
    expect(html).toContain('id="composer-documents-option-1"');
  });

  it("shows metadata that distinguishes same-named files", () => {
    const html = render({ query: "resume", results: [file({ file_name: "resume - previous.pdf" })] });
    expect(html).toContain("PDF");
    expect(html).toContain("594 B");
  });

  it("omits metadata the API did not provide rather than inventing it", () => {
    // `0` bytes means "not measured", and an absent date means unknown. Neither
    // may render as a placeholder.
    const html = render({
      query: "x",
      results: [file({ file_size_bytes: 0, created_at: undefined })],
    });
    expect(html).not.toContain("0 B");
    // The format is derived from the name, so it still shows.
    expect(html).toContain("PDF");
  });

  it("keeps the full name available when it is truncated for display", () => {
    const long = "a-very-long-document-name-that-will-not-fit-in-the-row.pdf";
    const html = render({ query: "a", results: [file({ file_name: long })] });
    expect(html).toContain(`title="${long}"`);
  });
});
