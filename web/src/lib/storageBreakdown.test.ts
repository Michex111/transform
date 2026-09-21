import { describe, expect, it } from "vitest";
import { aggregateStorageBreakdown, formatStoragePercent } from "@/lib/storageBreakdown";

// document 11534336 (pdf + docx) · image 4194304 · spreadsheet 2097152 ·
// audio 1048576 · archive 524288 · other 263168 (epub + extension-less)
const BREAKDOWN = [
  { extension: "pdf", bytes: 10485760, file_count: 2 },
  { extension: "docx", bytes: 1048576, file_count: 1 },
  { extension: "xlsx", bytes: 2097152, file_count: 1 },
  { extension: "png", bytes: 4194304, file_count: 3 },
  { extension: "mp3", bytes: 1048576, file_count: 1 },
  { extension: "zip", bytes: 524288, file_count: 1 },
  { extension: "epub", bytes: 262144, file_count: 1 },
  { extension: "", bytes: 1024, file_count: 1 },
];
const USED = BREAKDOWN.reduce((n, e) => n + e.bytes, 0);

describe("aggregateStorageBreakdown", () => {
  it("folds extensions into category buckets, largest first", () => {
    const segments = aggregateStorageBreakdown(BREAKDOWN, USED);
    expect(segments.map((s) => s.category)).toEqual([
      "document",
      "image",
      "spreadsheet",
      "audio",
      "archive",
      "other",
    ]);
  });

  it("sums bytes and file counts per bucket (docx merges into Documents)", () => {
    const [document] = aggregateStorageBreakdown(BREAKDOWN, USED);
    expect(document.label).toBe("Documents");
    expect(document.bytes).toBe(11534336);
    expect(document.fileCount).toBe(3);
    expect(document.color).toBe("var(--color-fmt-pdf)");
  });

  it("collapses unmapped formats and extension-less files into Other", () => {
    const other = aggregateStorageBreakdown(BREAKDOWN, USED).find((s) => s.category === "other");
    expect(other?.bytes).toBe(263168);
    expect(other?.color).toBe("var(--color-fmt-text)");
  });

  it("computes each percent against used_bytes", () => {
    const [, image] = aggregateStorageBreakdown(BREAKDOWN, USED);
    expect(image.percent).toBeCloseTo((4194304 / USED) * 100, 6);
    expect(formatStoragePercent(image.percent)).toBe("21%");
  });

  it("never yields NaN when used_bytes is 0", () => {
    const segments = aggregateStorageBreakdown(BREAKDOWN, 0);
    expect(segments).toHaveLength(6);
    for (const s of segments) expect(s.percent).toBeNull();
  });

  it.each([undefined, null, []])("treats %o as no data", (input) => {
    expect(aggregateStorageBreakdown(input, USED)).toEqual([]);
  });

  it("ignores non-positive or non-numeric byte counts", () => {
    expect(
      aggregateStorageBreakdown(
        [
          { extension: "pdf", bytes: 0, file_count: 1 },
          { extension: "png", bytes: Number.NaN, file_count: 1 },
          { extension: "zip", bytes: 2048, file_count: 1 },
        ],
        USED,
      ).map((s) => s.category),
    ).toEqual(["archive"]);
  });
});

describe("formatStoragePercent", () => {
  it("keeps the value readable across magnitudes", () => {
    expect(formatStoragePercent(56.33)).toBe("56%");
    expect(formatStoragePercent(5.333)).toBe("5.3%");
    expect(formatStoragePercent(0.34)).toBe("0.34%");
    expect(formatStoragePercent(0.0001)).toBe("<0.01%");
  });

  it("returns null rather than NaN or a bare 0%", () => {
    expect(formatStoragePercent(null)).toBeNull();
    expect(formatStoragePercent(0)).toBeNull();
    expect(formatStoragePercent(Number.NaN)).toBeNull();
  });
});
