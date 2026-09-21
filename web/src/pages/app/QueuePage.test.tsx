// Render-level tests for the Queue page's "active only" rule.
//
// The Queue is a live view: finished conversions move to History. These tests
// render the real page (with a stubbed job store) because the rule that matters
// is what actually reaches the DOM — a unit test of the filter alone would not
// catch the page rendering the unfiltered list.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { UiJob } from "@/jobs/jobStore";

// Rendering an effectful component tree through `renderToString` makes React log
// a `useLayoutEffect` warning (react-router, motion). It is expected for an SSR
// smoke test, so silence just that message and keep any real error visible.
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

const JOBS: UiJob[] = [
  {
    job_id: "running",
    status: "PROCESSING",
    source_format: "pdf",
    target_format: "docx",
    input_file: "running-report.pdf",
    output_file: null,
    object_key: "uploads/running.pdf",
    download_url: null,
    progress: 42,
  },
  {
    job_id: "queued",
    status: "PENDING",
    source_format: "png",
    target_format: "jpg",
    input_file: "queued-photo.png",
    output_file: null,
    object_key: "uploads/queued.png",
    download_url: null,
  },
  {
    job_id: "done",
    status: "COMPLETED",
    source_format: "docx",
    target_format: "pdf",
    input_file: "finished-contract.docx",
    output_file: "output/finished-contract.pdf",
    object_key: "uploads/finished.docx",
    download_url: "https://example.invalid/contract.pdf",
    credits_used: 12,
  },
  {
    job_id: "broke",
    status: "FAILED",
    source_format: "xlsx",
    target_format: "csv",
    input_file: "failed-budget.xlsx",
    output_file: null,
    object_key: "uploads/failed.xlsx",
    download_url: null,
    errorMessage: "converter crashed",
  },
];

// The page reads the shared store; stub it so the test controls the job set.
vi.mock("@/jobs/JobsContext", () => ({
  useJobs: () => ({ jobs: JOBS, refresh: () => {}, updateJob: () => {}, removeJob: () => {} }),
}));

const { QueuePage } = await import("@/pages/app/QueuePage");

/** React splits text and interpolation with comment markers; drop them. */
function stripReactComments(html: string): string {
  return html.replace(/<!-- -->/g, "");
}

function render(): string {
  return stripReactComments(
    renderToString(
      <MemoryRouter>
        <QueuePage />
      </MemoryRouter>,
    ),
  );
}

describe("QueuePage", () => {
  it("renders the in-progress conversions", () => {
    const html = render();
    expect(html).toContain("running-report.pdf");
    expect(html).toContain("queued-photo.png");
  });

  it("does not render completed conversions", () => {
    const html = render();
    expect(html).not.toContain("finished-contract.docx");
  });

  it("does not render failed conversions", () => {
    const html = render();
    expect(html).not.toContain("failed-budget.xlsx");
    expect(html).not.toContain("converter crashed");
  });

  it("counts only the active conversions", () => {
    expect(render()).toContain("2 active");
  });

  it("drops the now-unreachable terminal-state actions", () => {
    const html = render();
    // Download/Retry belong to History once the queue is active-only.
    expect(html).not.toContain('aria-label="Download"');
    expect(html).not.toContain(">Retry<");
  });

  it("points at History when nothing is running", () => {
    expect(render()).not.toContain("Finished conversions appear in");
  });
});

describe("QueuePage with nothing in flight", () => {
  it("shows the empty state and links to History", async () => {
    vi.resetModules();
    vi.doMock("@/jobs/JobsContext", () => ({
      // Only terminal jobs — exactly the state after everything finishes.
      useJobs: () => ({ jobs: JOBS.filter((j) => j.status === "COMPLETED"), refresh: () => {} }),
    }));
    const { QueuePage: Page } = await import("@/pages/app/QueuePage");

    const html = stripReactComments(
      renderToString(
        <MemoryRouter>
          <Page />
        </MemoryRouter>,
      ),
    );

    expect(html).toContain("Nothing is running right now.");
    expect(html).toContain('href="/app/history"');
    expect(html).toContain("0 active");
  });
});
