// The workflow editor's format control.
//
// This exists because of a specific defect: the editor offered a native
// `<select>` of ~67 two-to-five letter format codes, which cannot be searched,
// shows no indication of what a format *is*, and looked nothing like the picker
// the Convert and guest pages use for the same decision.
//
// The popover is stateful (it opens on click) and this project renders without a
// DOM, so what a server render can prove is the *trigger*: that the app's own
// picker is what is wired up, with the right accessible name and a valid
// initial selection. The option list itself is covered by
// `formatPickerOptions.test.ts` and `workflowTargets.test.ts`.
//
// The page around the editor is not rendered here: it fetches its list in an
// effect (which a server render does not run) and renders skeletons on the
// first pass, so rendering it would prove nothing about this control.

import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";

// The editor reaches for the API client and the conversion graph. Both are
// provided by context in the app; neither is available in a server render, so
// they are stubbed at the module boundary — which is also what keeps this test
// from needing a provider tree it is not here to exercise.
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    api: { createWorkflow: vi.fn(), updateWorkflow: vi.fn() },
    user: { id: 1 },
  }),
}));

vi.mock("@/lib/useConversionMap", () => ({
  useConversionMap: () => ({
    // A realistic spread: document, image and archive targets, so the picker's
    // category restriction has something in more than one bucket to work with.
    targets: ["pdf", "docx", "png", "zip", "tar.gz"],
    sources: ["pdf"],
    map: { pdf: ["docx", "png"] },
    supported: new Set(["pdf", "docx", "png", "zip", "tar.gz"]),
    targetsFor: () => ["docx", "png"],
    sourcesFor: () => [],
    loading: false,
    error: null,
    stale: false,
  }),
}));

const { WorkflowEditorModal } = await import("@/pages/app/WorkflowsPage");

function renderEditor(): string {
  return renderToString(
    <WorkflowEditorModal open workflow={null} onClose={() => {}} onSaved={() => {}} />,
  );
}

describe("WorkflowEditorModal format control", () => {
  it("uses the app's format picker rather than a native select", () => {
    const html = renderEditor();
    // The regression: a plain `<select>` of format codes.
    expect(html).not.toContain("<select");
    // …replaced by the picker the rest of the app uses for this decision.
    expect(html).toContain('aria-label="Choose target format"');
    expect(html).toContain('aria-expanded="false"');
  });

  it("opens on a format that is actually allowed", () => {
    // `pdf` is a target in the graph above, so the trigger must be enabled. A
    // mis-cased or mistyped default would render it disabled — which reads as a
    // broken dialog rather than as a bad list.
    const html = renderEditor();
    expect(html).toContain('aria-disabled="false"');
    // Not busy: the graph has arrived, so the "Loading…" affordance must be off.
    expect(html).toContain('aria-busy="false"');
  });

  it("does not claim a control it does not implement", () => {
    // The picker is a grid of toggle buttons with a category sidebar and a
    // search box, not a listbox, and it has no arrow-key model.
    expect(renderEditor()).not.toContain('aria-haspopup="listbox"');
  });

  it("labels the control for assistive technology, since the visible label is prose", () => {
    const html = renderEditor();
    expect(html).toContain("Convert every selected file to");
    // The visible sentence is not a `<label for>` any more (there is no form
    // element to point at), so the name has to come from the control itself.
    expect(html).toContain('aria-label="Choose target format"');
  });

  it("still offers the name and description fields", () => {
    // Guards the surrounding form against being lost while swapping the picker.
    const html = renderEditor();
    expect(html).toContain('id="workflow-name"');
    expect(html).toContain('id="workflow-description"');
  });
});
