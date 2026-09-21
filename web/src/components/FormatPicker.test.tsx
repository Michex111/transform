// Render tests for the format picker's "unknown allowed set" signalling.
//
// The picker's popover is stateful (it opens on click) and this project renders
// without a DOM, so these tests assert the trigger's accessibility contract —
// which is what tells a user (and assistive tech) that nothing may be picked
// yet. The option list itself is covered by `formatPickerOptions.test.ts`.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { FormatIcon, FormatPicker } from "@/components/FormatPicker";

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

  it("does not claim a listbox it does not implement", () => {
    // The popover is a 3-column grid of toggle buttons (plus a category sidebar
    // and a search box), not a linear listbox, and it has no arrow-key model —
    // so the trigger must not advertise `aria-haspopup="listbox"`.
    const html = renderPicker({ value: "pdf", onChange: () => {} });
    expect(html).not.toContain("aria-haspopup");
    // The popup's open state is still conveyed.
    expect(html).toContain('aria-expanded="false"');
  });
});

describe("FormatIcon", () => {
  it("tints its tile with a real, computed colour", () => {
    // `${color}1a` / `${color}40` on a `var(--token)` colour is invalid CSS and
    // was dropped, leaving the tile untinted.
    const html = renderToString(<FormatIcon ext="pdf" />);
    expect(html).toContain("color-mix(");
    expect(html).not.toContain("var(--color-fmt-pdf)1a");
    expect(html).not.toContain("1px solid var(--color-fmt-pdf)40");
  });

  it("resolves a format case-insensitively, like every other helper", () => {
    expect(renderToString(<FormatIcon ext="PDF" />)).toBe(
      renderToString(<FormatIcon ext="pdf" />),
    );
  });
});
