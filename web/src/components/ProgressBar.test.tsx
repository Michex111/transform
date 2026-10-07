// Server-render smoke tests for the shared ProgressBar.
//
// The two states are the ones the whole app depends on: a real percentage
// (determinate) and "no value yet" (indeterminate, still moving). They must be
// distinguishable in the DOM — the determinate bar reports `aria-valuenow`, the
// indeterminate one must NOT, so a screen reader never hears an invented number
// (that was a shipped bug: the pages passed a made-up 45).

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ProgressBar } from "@/components/ui";

describe("ProgressBar", () => {
  it("renders a determinate bar for a real percentage", () => {
    const html = renderToString(<ProgressBar value={50} ariaLabel="conversion progress" />);
    expect(html).toContain('aria-valuenow="50"');
    expect(html).toContain('aria-valuetext="50%"');
    // The indeterminate sweep must not be present on a determinate bar.
    expect(html).not.toContain("In progress");
    expect(html).not.toContain("w-1/3");
  });

  it("renders a still-moving indeterminate bar when there is no value", () => {
    const html = renderToString(<ProgressBar value={null} ariaLabel="conversion progress" />);
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('aria-valuetext="In progress"');
    // No invented number is announced.
    expect(html).not.toContain("aria-valuenow");
  });

  it("clamps and rounds the reported value", () => {
    expect(renderToString(<ProgressBar value={120} ariaLabel="x" />)).toContain(
      'aria-valuenow="100"',
    );
    expect(renderToString(<ProgressBar value={-5} ariaLabel="x" />)).toContain(
      'aria-valuenow="0"',
    );
    expect(renderToString(<ProgressBar value={42.4} ariaLabel="x" />)).toContain(
      'aria-valuenow="42"',
    );
  });
});
