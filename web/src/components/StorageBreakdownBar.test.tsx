// Server-render smoke tests for the storage breakdown bar.
//
// Two states matter and both are covered here: the field present (the backend
// has landed) and the field absent (today's backend, or an account with no
// categorised files). The component renders nothing in the second case, which
// is what lets the dashboard keep its plain `ProgressBar` fallback.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { StorageBreakdownBar } from "@/components/StorageBreakdownBar";

const PROPS = { usedBytes: 18612224, limitBytes: 5368709120, usedPercent: 0.347 };

const BREAKDOWN = [
  { extension: "pdf", bytes: 10485760, file_count: 2 },
  { extension: "xlsx", bytes: 2097152, file_count: 1 },
  { extension: "png", bytes: 4194304, file_count: 3 },
  { extension: "mp3", bytes: 1048576, file_count: 1 },
  { extension: "zip", bytes: 524288, file_count: 1 },
  { extension: "epub", bytes: 262144, file_count: 1 },
];

describe("StorageBreakdownBar", () => {
  it("renders a segment and a legend row per category", () => {
    const html = renderToString(<StorageBreakdownBar {...PROPS} breakdown={BREAKDOWN} />);
    for (const label of [
      "Documents",
      "Spreadsheets",
      "Images",
      "Audio",
      "Archives",
      "Other",
    ]) {
      expect(html).toContain(label);
    }
  });

  it("exposes the quota meaning and focuses each segment by keyboard", () => {
    const html = renderToString(<StorageBreakdownBar {...PROPS} breakdown={BREAKDOWN} />);
    expect(html).toContain('role="group"');
    // The bar encodes composition, so the quota reading lives in the label and
    // in the caption under the bar.
    expect(html).toContain("Storage composition: 18 MB of 5.0 GB used (0.35%)");
    expect(html).toContain("of quota");
    // Every segment is reachable by Tab and carries its own description.
    expect(html).toContain('tabindex="0"');
    expect(html).toContain("56% of storage used");
  });

  it("fills the whole track so every category stays hoverable", () => {
    const html = renderToString(<StorageBreakdownBar {...PROPS} breakdown={BREAKDOWN} />);
    // Segments size themselves with flex-grow, not with a percentage of the
    // quota — otherwise a 0.35% account would leave them sub-pixel.
    expect(html).toContain("flex h-full w-full");
    expect(html).toContain("flex-grow:10485760");
  });

  it("hides the tooltip until a segment is hovered or focused", () => {
    const html = renderToString(<StorageBreakdownBar {...PROPS} breakdown={BREAKDOWN} />);
    expect(html).not.toContain("bottom-full");
  });

  it.each([undefined, null, []])("renders nothing when breakdown is %o", (breakdown) => {
    expect(
      renderToString(<StorageBreakdownBar {...PROPS} breakdown={breakdown} />),
    ).toBe("");
  });
});
