// The file row inside a conversion card.
//
// The class-level contract is what matters here (the suite runs in the Node
// environment, so components are rendered with `renderToString` — the same
// convention as `AttachmentChips.test.tsx`): a name is always recoverable, a
// size the worker never measured is never rendered, and a result that does not
// exist yet is described rather than named.
//
// The card's source/result naming rule is pure logic and is tested separately in
// `@/lib/conversionCard.test.ts`.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ConversionFileRow } from "@/components/assistant/ConversionCard";

describe("ConversionFileRow", () => {
  it("shows the filename and keeps it recoverable when truncated", () => {
    const html = renderToString(
      <ConversionFileRow
        label="Converted file"
        format="docx"
        filename="upei residence.docx"
      />,
    );
    expect(html).toContain("Converted file");
    expect(html).toContain("upei residence.docx");
    // Truncation for display, but the full name is in the tooltip and in the
    // DOM — hover must never be the only route to a file's real name.
    expect(html).toContain('title="upei residence.docx"');
  });

  it("renders the pending label instead of a filename when there is none", () => {
    const html = renderToString(
      <ConversionFileRow
        label="Converted file"
        format="docx"
        filename={null}
        pendingLabel="DOCX pending"
      />,
    );
    expect(html).toContain("DOCX pending");
    // No tooltip, because there is no name to recover.
    expect(html).not.toContain("title=");
  });

  it("shows a size only when the worker actually measured one", () => {
    const measured = renderToString(
      <ConversionFileRow label="Original" format="pdf" filename="a.pdf" sizeBytes={1_887_437} />,
    );
    expect(measured).toContain("1.8 MB");

    // `0` means "not measured" on the wire (see `ConversionJobResponse`), so it
    // must be omitted rather than rendered as a zero-byte file.
    const unmeasured = renderToString(
      <ConversionFileRow label="Original" format="pdf" filename="a.pdf" sizeBytes={0} />,
    );
    expect(unmeasured).not.toContain("0 B");
  });
});
