// Render-level tests for the token (credit) cost shown on History rows.
//
// The Queue already reported this for completed conversions; History is where a
// finished conversion lives once the Queue is active-only, so the cost has to be
// visible here. These render the real page because the rule that matters is what
// reaches the DOM.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import type { UiJob } from "@/jobs/jobStore";

const JOBS: UiJob[] = [
  {
    job_id: "billed",
    status: "COMPLETED",
    source_format: "docx",
    target_format: "pdf",
    input_file: "billed-contract.docx",
    output_file: "output/billed-contract.pdf",
    object_key: "uploads/billed.docx",
    download_url: "https://example.invalid/contract.pdf",
    credits_used: 12,
  },
  {
    job_id: "free",
    status: "COMPLETED",
    source_format: "txt",
    target_format: "md",
    input_file: "free-notes.txt",
    output_file: "output/free-notes.md",
    object_key: "uploads/free.txt",
    download_url: "https://example.invalid/notes.md",
    credits_used: 0,
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
    credits_used: 9,
    errorMessage: "converter crashed",
  },
  {
    job_id: "running",
    status: "PROCESSING",
    source_format: "png",
    target_format: "jpg",
    input_file: "running-photo.png",
    output_file: null,
    object_key: "uploads/running.png",
    download_url: null,
    progress: 30,
  },
];

vi.mock("@/jobs/JobsContext", () => ({
  useJobs: () => ({ jobs: JOBS, refresh: () => {}, updateJob: () => {}, removeJob: () => {} }),
}));

vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    api: {
      downloadConvertedFile: vi.fn(),
      deleteHistoryJob: vi.fn(),
      objectExists: vi.fn(),
      retryJob: vi.fn(),
      convertWithFile: vi.fn(),
    },
    user: null,
  }),
}));

vi.mock("@/auth/ToastContext", () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

const { HistoryPage } = await import("@/pages/app/HistoryPage");

// `renderToString` on an effectful tree makes React log a `useLayoutEffect`
// warning (react-router, motion). Expected here; silence just that message.
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

/** React splits text and interpolation with comment markers; drop them. */
function stripReactComments(html: string): string {
  return html.replace(/<!-- -->/g, "");
}

function render(): string {
  return stripReactComments(
    renderToString(
      <MemoryRouter>
        <HistoryPage />
      </MemoryRouter>,
    ),
  );
}

describe("HistoryPage token cost", () => {
  it("shows the tokens a completed conversion used", () => {
    const html = render();
    expect(html).toContain("billed-contract.docx");
    expect(html).toContain('aria-label="12 tokens used"');
    expect(html).toContain('title="Tokens used"');
  });

  it("hides tokens for a completed conversion that used none", () => {
    const html = render();
    expect(html).toContain("free-notes.txt");
    expect(html).not.toContain('aria-label="0 tokens used"');
  });

  it("hides tokens for a failed conversion, which is never charged", () => {
    expect(render()).not.toContain('aria-label="9 tokens used"');
  });

  it("shows exactly one token badge — not on the running or failed rows", () => {
    // The billed job is the only row entitled to a badge, so its value is the
    // whole set. This covers the still-running row too, which has no cost yet.
    const badges = render().match(/aria-label="\d+ tokens used"/g) ?? [];
    expect(badges).toEqual(['aria-label="12 tokens used"']);
  });
});
