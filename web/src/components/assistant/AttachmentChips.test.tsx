// The attached-file chip must stay COMPACT ON A TOUCH DEVICE.
//
// It did not, and the reason is worth pinning: the remove button carried
// `pointer-coarse:min-h-11 pointer-coarse:min-w-11`, which reads like "a 44px
// target without a 44px chip" but does not behave that way. `min-height` on a
// flex child sets the PARENT's height too, so the whole chip rendered ~49px tall
// on a phone — noticeably taller than the same chip on a desktop, which is
// exactly backwards.
//
// The fix moves the target onto an absolutely positioned `::before`, which
// cannot affect layout. There is no layout engine here (the suite runs in the
// Node environment), so these tests assert the *contract on the classes* rather
// than a measured height; the corresponding measurement was verified in a
// browser at 393x852 with a coarse pointer (chip 49.3px -> 21.3px, and the
// invisible expander hit-tested as a real 44x44 target).

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { AttachmentChips } from "@/components/assistant/AttachmentChips";
import type { AssistantAttachment } from "@/api/types";

const ATTACHMENT: AssistantAttachment = {
  id: "objects/upei residence.pdf",
  name: "upei residence.pdf",
  extension: "pdf",
};

/** The `<button>` tag for the chip's ✕, so assertions can be scoped to it. */
function removeButtonTag(html: string): string {
  return html.match(/<button[^>]*aria-label="Remove[^"]*"[^>]*>/)?.[0] ?? "";
}

describe("AttachmentChips", () => {
  it("renders the file name in the chip", () => {
    const html = renderToString(
      <AttachmentChips attachments={[ATTACHMENT]} onRemove={() => {}} />,
    );
    expect(html).toContain("upei residence.pdf");
  });

  it("keeps the remove button's touch target out of the layout", () => {
    const button = removeButtonTag(
      renderToString(<AttachmentChips attachments={[ATTACHMENT]} onRemove={() => {}} />),
    );
    expect(button).toBeTruthy();
    // The expander is the whole 44px target, and it is layout-free by
    // construction: absolutely positioned, no box of its own in the flow.
    expect(button).toContain("pointer-coarse:before:absolute");
    expect(button).toContain("pointer-coarse:before:-inset-3.5");
    // …and the button must NOT size itself on touch, because sizing it is what
    // stretched the chip. A `pointer-coarse:min-h-*`/`min-w-*` here is the
    // regression, whatever value it carries.
    expect(button).not.toMatch(/pointer-coarse:min-[hw]-/);
  });

  it("still exposes an accessible name for the remove action", () => {
    // Hiding the label text is fine; the name must survive for a screen reader.
    const button = removeButtonTag(
      renderToString(<AttachmentChips attachments={[ATTACHMENT]} onRemove={() => {}} />),
    );
    expect(button).toContain('aria-label="Remove upei residence.pdf"');
  });

  it("renders no remove control for the read-only transcript variant", () => {
    const html = renderToString(<AttachmentChips attachments={[ATTACHMENT]} />);
    expect(html).toContain("upei residence.pdf");
    expect(html).not.toContain("Remove upei residence.pdf");
  });

  it("collapses a long list behind a +N more toggle", () => {
    const many = Array.from({ length: 5 }, (_, index) => ({
      id: `f-${index}`,
      name: `file-${index}.pdf`,
      extension: "pdf",
    }));
    const html = renderToString(
      <AttachmentChips attachments={many} collapsible limit={3} />,
    );
    expect(html).toContain("+2 more");
    // Collapsed means collapsed: only `limit` chips are rendered, so a five-file
    // turn cannot grow the bubble. The overflow count is the only hint of them.
    expect(html).toContain("file-2.pdf");
    expect(html).not.toContain("file-3.pdf");
  });

  it("renders nothing when there are no attachments", () => {
    expect(renderToString(<AttachmentChips attachments={[]} />)).toBe("");
  });
});
