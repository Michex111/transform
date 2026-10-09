// Tests for the format-picker restriction rules.
//
// The rule under test is the one that caused a real bug: the allowed list comes
// from the backend conversion graph, so it is empty until that graph arrives.
// An empty list used to mean "no restriction", which offered the entire static
// catalogue — including pairs the server rejects with a 400.

import { describe, expect, it } from "vitest";
import { FORMAT_CATEGORIES } from "@/lib/formatCatalog";
import {
  categoryForFormat,
  isPickableFormat,
  restrictFormatCategories,
} from "@/lib/formatPickerOptions";

const ALL_EXTS = FORMAT_CATEGORIES.flatMap((category) =>
  category.formats.map((format) => format.ext),
);

describe("restrictFormatCategories", () => {
  it("returns the whole catalogue when the picker is unrestricted", () => {
    expect(restrictFormatCategories(undefined)).toEqual(FORMAT_CATEGORIES);
  });

  it("offers nothing when the allowed list is empty", () => {
    // The regression guard: an empty list must NOT fall back to the catalogue.
    expect(restrictFormatCategories([])).toEqual([]);
  });

  it("keeps only the allowed formats and drops emptied categories", () => {
    const categories = restrictFormatCategories(["png", "pdf"]);

    const offered = categories.flatMap((category) => category.formats.map((f) => f.ext));
    expect(offered.sort()).toEqual(["pdf", "png"]);
    // Every surviving category actually contains an allowed format.
    expect(categories.every((category) => category.formats.length > 0)).toBe(true);
  });

  it("matches extensions case-insensitively", () => {
    const offered = restrictFormatCategories(["PNG"]).flatMap((category) =>
      category.formats.map((f) => f.ext),
    );
    expect(offered).toEqual(["png"]);
  });

  it("ignores allowed formats that are not in the catalogue", () => {
    expect(restrictFormatCategories(["notarealformat"])).toEqual([]);
  });

  it("preserves the catalogue's category and format order", () => {
    const categories = restrictFormatCategories(ALL_EXTS);
    expect(categories.map((category) => category.id)).toEqual(
      FORMAT_CATEGORIES.map((category) => category.id),
    );
  });
});

describe("isPickableFormat", () => {
  it("accepts anything for an unrestricted picker", () => {
    expect(isPickableFormat(undefined, "pdf")).toBe(true);
  });

  it("accepts only listed formats for a restricted picker", () => {
    expect(isPickableFormat(["png", "jpg"], "png")).toBe(true);
    expect(isPickableFormat(["png", "jpg"], "PNG")).toBe(true);
    expect(isPickableFormat(["png", "jpg"], "docx")).toBe(false);
  });

  it("rejects the current selection when nothing is allowed yet", () => {
    expect(isPickableFormat([], "pdf")).toBe(false);
  });
});

describe("categoryForFormat", () => {
  it("finds the category holding a format", () => {
    // The defect this exists for: the picker opened on `categories[0]`
    // ("Archive"), so changing an existing PDF showed BZ2/GZ/TAR.
    expect(categoryForFormat("pdf")).toBe("document");
    expect(categoryForFormat("jpg")).toBe("image");
    expect(categoryForFormat("zip")).toBe("archive");
  });

  it("matches case-insensitively", () => {
    // A stored value can be "PDF"; a case-sensitive lookup would answer "no
    // category" for a format the picker can display perfectly well.
    expect(categoryForFormat("PDF")).toBe("document");
    expect(categoryForFormat("  Pdf  ")).toBe("document");
  });

  it("returns null for a format in no category", () => {
    expect(categoryForFormat("not-a-format")).toBeNull();
    expect(categoryForFormat("")).toBeNull();
  });

  it("honours a restricted category list", () => {
    // The picker passes its *restricted* categories, so a format whose category
    // was filtered out must report no category rather than a hidden one.
    const restricted = restrictFormatCategories(["pdf", "docx"]);
    expect(categoryForFormat("pdf", restricted)).toBe("document");
    expect(categoryForFormat("zip", restricted)).toBeNull();
  });
});
