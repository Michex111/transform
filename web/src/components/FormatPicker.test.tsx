// Render tests for the format picker's "unknown allowed set" signalling.
//
// The picker's popover is stateful (it opens on click) and this project renders
// without a DOM, so these tests assert the trigger's accessibility contract —
// which is what tells a user (and assistive tech) that nothing may be picked
// yet. The option list itself is covered by `formatPickerOptions.test.ts`.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { FormatPicker } from "@/components/FormatPicker";

function renderPicker(props: Parameters<typeof FormatPicker>[0]) {
  return renderToString(<FormatPicker {...props} />);
}

describe("FormatPicker trigger", () => {
  it("is enabled when no allowed list is given", () => {
    const html = renderPicker({ value: "pdf", onChange: () => {} });
    expect(html).toContain('aria-disabled="false"');
    expect(html).toContain('aria-busy="false"');
  });

  it("is disabled while the allowed list is empty and not yet resolved", () => {
    const html = renderPicker({ value: "pdf", onChange: () => {}, allowed: [], pending: true });
    expect(html).toContain('aria-disabled="true"');
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain("Loading the supported formats");
  });

  it("is disabled with an unavailable message when the allowed list is empty", () => {
    const html = renderPicker({ value: "pdf", onChange: () => {}, allowed: [] });
    expect(html).toContain('aria-disabled="true"');
    expect(html).toContain("No supported format is available");
  });

  it("stays enabled when the current value is allowed", () => {
    const html = renderPicker({ value: "png", onChange: () => {}, allowed: ["png", "jpg"] });
    expect(html).toContain('aria-disabled="false"');
  });

  it("is disabled when the current value is not allowed", () => {
    const html = renderPicker({ value: "pdf", onChange: () => {}, allowed: ["png", "jpg"] });
    expect(html).toContain('aria-disabled="true"');
  });
});
