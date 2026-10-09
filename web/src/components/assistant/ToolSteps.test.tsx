// The activity disclosure above an answer.
//
// It used to read "Show 2 steps", which describes the widget rather than what
// the user gets from opening it. The tests pin the replacement wording, the
// accessible name that carries the count, and the two rules that make the
// disclosure safe: the panel is collapsed by default (the answer stays the
// focus) but is always in the DOM, so `aria-controls` never dangles and nothing
// is hidden from a screen reader without a control to reveal it.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import type { AssistantStep } from "@/lib/assistantChat";
import { ToolSteps } from "@/components/assistant/ToolSteps";

const STEPS: AssistantStep[] = [
  { name: "search_files", label: "Searching your files", summary: "Found 4 files" },
  { name: "read_file", label: "Reading a document", summary: "" },
];

describe("ToolSteps", () => {
  it("labels the disclosure by what opening it gives the user", () => {
    const html = renderToString(<ToolSteps steps={STEPS} />);
    expect(html).toContain("View activity");
    expect(html).not.toContain("Show 2 steps");
  });

  it("carries the step count in the accessible name", () => {
    // The visible label is short, so the number must not be lost to a screen
    // reader along with the words that used to hold it.
    const html = renderToString(<ToolSteps steps={STEPS} />);
    expect(html).toContain('aria-label="View activity, 2 steps"');
  });

  it("singularises the count", () => {
    const html = renderToString(<ToolSteps steps={[STEPS[0]]} />);
    expect(html).toContain('aria-label="View activity, 1 step"');
  });

  it("collapses the panel by default but keeps it in the DOM", () => {
    const html = renderToString(<ToolSteps steps={STEPS} />);
    expect(html).toContain('aria-expanded="false"');
    // Present, and labelled by the trigger: `aria-controls` points at a real
    // element whether or not it is currently shown. Assert on the `<ol>` itself
    // — a bare `toContain("hidden")` would also match the caret's `aria-hidden`.
    const list = html.match(/<ol[^>]*>/)?.[0] ?? "";
    expect(list).toBeTruthy();
    expect(list).toContain("hidden");
  });

  it("renders the step labels for a reader who opens it", () => {
    const html = renderToString(<ToolSteps steps={STEPS} />);
    expect(html).toContain("Searching your files");
    expect(html).toContain("Found 4 files");
  });

  it("renders nothing when the turn recorded no steps", () => {
    expect(renderToString(<ToolSteps steps={[]} />)).toBe("");
  });
});
