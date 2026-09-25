// Render tests for the disclosure panel a narrow job row expands into.
//
// This is the layout that has no unit-testable "logic" of its own — what matters
// is what reaches the DOM: the detail lines, the actions, and the fact that a
// collapsed panel contributes *nothing*. That last one is load-bearing beyond
// performance: the desktop row already renders a token badge and a delete
// button, so an always-mounted panel would put two elements with the same
// accessible name in the document.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { JobDetailsPanel } from "@/components/JobDetailsPanel";
import type { UiJob } from "@/jobs/JobsContext";

const JOB: UiJob = {
  job_id: "job-1",
  status: "COMPLETED",
  source_format: "mp3",
  target_format: "aac",
  input_file: "Maroon 5 - Memories (Official Video) - (320 Kbps) (1).mp3",
  output_file: "outputs/user/7/job/job-1/memories.aac",
  object_key: "uploads/memories.mp3",
  download_url: "https://example.invalid/memories.aac",
  credits_used: 4,
  compute_duration_ms: 1234,
  input_size_bytes: 4_300_000,
  output_size_bytes: 1_100_000,
  createdAt: "2026-09-21T13:45:00Z",
};

// `renderToString` on a tree containing motion's layout effects makes React log
// a `useLayoutEffect` warning. Expected here; silence just that message.
const realConsoleError = console.error;
beforeEach(() => {
  console.error = (...args: unknown[]) => {
    if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
      return;
    }
    realConsoleError(...args);
  };
});
afterEach(() => {
  console.error = realConsoleError;
});

const stripReactComments = (html: string) => html.replace(/<!-- -->/g, "");

/**
 * Render the panel.
 *
 * `compact` is the card layout below `md`; it is also what gates the panel's
 * own Delete/Retry buttons, because the desktop row renders those inline and
 * two delete buttons for one record is worse than one.
 */
function render(
  job: UiJob,
  { open = true, compact = true, savingToDrive = false, handlers = {} }: {
    open?: boolean;
    compact?: boolean;
    savingToDrive?: boolean;
    handlers?: { onDelete?: boolean; onRetry?: boolean; onSaveToDrive?: boolean };
  } = {},
) {
  return stripReactComments(
    renderToString(
      <JobDetailsPanel
        open={open}
        job={job}
        id="row-details"
        compact={compact}
        savingToDrive={savingToDrive}
        onDelete={handlers.onDelete ? vi.fn() : undefined}
        onRetry={handlers.onRetry ? vi.fn() : undefined}
        onSaveToDrive={handlers.onSaveToDrive ? vi.fn() : undefined}
      />,
    ),
  );
}

describe("JobDetailsPanel", () => {
  it("contributes nothing to the document while the row is collapsed", () => {
    expect(render(JOB, { open: false })).toBe("");
  });

  it("reports the conversion, its sizes, cost, duration and time", () => {
    const html = render(JOB);
    expect(html).toContain("Conversion");
    expect(html).toContain("MP3 to AAC"); // FormatMorph's accessible name
    expect(html).toContain("Input size");
    expect(html).toContain("4.1 MB");
    expect(html).toContain("Output size");
    expect(html).toContain("1.0 MB");
    expect(html).toContain('aria-label="4 tokens used"');
    expect(html).toContain("Duration");
    expect(html).toContain("1.2 s");
    expect(html).toContain("Converted");
  });

  it("names the file the conversion produced", () => {
    expect(render(JOB)).toContain("memories.aac");
  });

  it("flags an end-to-end encrypted job", () => {
    expect(render(JOB)).not.toContain("End-to-end");
    expect(render({ ...JOB, client_encrypted: true })).toContain("End-to-end");
  });

  it("prints the file name in full, since the row it came from truncates it", () => {
    expect(render(JOB)).toContain("Maroon 5 - Memories (Official Video) - (320 Kbps) (1).mp3");
  });

  it("is a labelled region at the id the row's disclosure points at", () => {
    const html = render(JOB);
    expect(html).toContain('id="row-details"');
    expect(html).toContain('role="region"');
    expect(html).toContain('aria-label="Details for Maroon 5 - Memories (Official Video) - (320 Kbps) (1).mp3"');
  });

  it("offers delete and retry as real named buttons in the card layout", () => {
    const html = render({ ...JOB, status: "FAILED" }, { handlers: { onDelete: true, onRetry: true } });
    expect(html).toContain("<button");
    expect(html).toContain("Delete");
    expect(html).toContain("Retry");
  });

  it("leaves the actions to the row in the wide layout", () => {
    // The `md`+ row has its own Delete/Retry in its actions column; repeating
    // them inside the disclosure would give one record two delete buttons.
    const html = render(JOB, {
      compact: false,
      handlers: { onDelete: true, onRetry: true },
    });
    expect(html).not.toContain("Delete");
    expect(html).not.toContain("Retry");
    // …while the information — the whole point of the panel — is still there.
    expect(html).toContain("Tokens used");
    expect(html).toContain("Input size");
    expect(html).toContain("Output size");
  });

  it("still reports a failure in the wide layout, where the row only has a popover", () => {
    const html = render(
      { ...JOB, status: "FAILED", errorMessage: "converter crashed" },
      { compact: false, handlers: { onDelete: true, onRetry: true } },
    );
    expect(html).toContain("converter crashed");
    expect(html).not.toContain("Delete");
  });

  it("offers no retry for a job that has not failed", () => {
    const html = render(JOB, { handlers: { onDelete: true, onRetry: true } });
    expect(html).toContain("Delete");
    expect(html).not.toContain("Retry");
  });

  it("shows the failure as text, which a touch device can actually read", () => {
    const html = render({ ...JOB, status: "FAILED", errorMessage: "converter crashed" });
    expect(html).toContain("converter crashed");
  });

  it("has no action row when the caller passes no handlers", () => {
    const html = render(JOB);
    expect(html).not.toContain("Delete");
    expect(html).not.toContain("Retry");
    expect(html).not.toContain("Save to Drive");
  });

  it("offers Save to Drive for a completed job when the caller provides it", () => {
    // History passes this; the Dashboard does not, so the button is the caller's
    // decision rather than something the panel infers.
    expect(render(JOB, { handlers: { onSaveToDrive: true } })).toContain("Save to Drive");
  });

  it("offers no Save to Drive when the caller passes no handler", () => {
    const html = render(JOB, { handlers: { onDelete: true, onRetry: true } });
    expect(html).not.toContain("Save to Drive");
  });

  it("offers no Save to Drive for a job with no output to file", () => {
    // A running or failed job has nothing to save; queueing an upload for it
    // would only fill the dock with a transfer that cannot succeed.
    for (const status of ["PROCESSING", "PENDING", "FAILED"]) {
      expect(render({ ...JOB, status }, { handlers: { onSaveToDrive: true } })).not.toContain(
        "Save to Drive",
      );
    }
  });

  it("keeps Save to Drive in the wide layout, where it has no inline counterpart", () => {
    // The `md`+ row renders Delete/Retry itself (so the panel drops those), but
    // it has no save control of its own — hiding this one too would make the
    // feature unreachable on a desktop.
    const html = render(JOB, {
      compact: false,
      handlers: { onDelete: true, onRetry: true, onSaveToDrive: true },
    });
    expect(html).toContain("Save to Drive");
    expect(html).not.toContain("Delete");
  });

  it("says 'Saving…' while the output is being fetched", () => {
    const html = render(JOB, { savingToDrive: true, handlers: { onSaveToDrive: true } });
    expect(html).toContain("Saving…");
    expect(html).not.toContain("Save to Drive");
  });
});
