// Render-level tests for the token (credit) cost shown on History rows.
//
// The Queue already reported this for completed conversions; History is where a
// finished conversion lives once the Queue is active-only, so the cost has to be
// visible here. These render the real page because the rule that matters is what
// reaches the DOM.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { withTimeline } from "@/lib/historyFilters";
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

// History now offers "Save to Drive" from the details panel, and the shared
// `useSaveToDrive` hook reads the app-wide upload queue (the background manager
// that performs the transfer). `renderToString` runs no effects, so none of
// these is ever called — they only have to exist for the hook to mount.
vi.mock("@/uploads/uploadsContext", () => ({
  useUploads: () => ({
    addFiles: vi.fn(),
    uploads: [],
    cancel: vi.fn(),
    retry: vi.fn(),
    dismiss: vi.fn(),
    refreshLimits: vi.fn(),
    limits: { maxFileSizeBytes: null, availableBytes: null },
  }),
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

/**
 * Render History as if navigated to, optionally carrying router state.
 *
 * `renderToString` does not run effects, so this asserts the *initial* window
 * (what the dropdown shows on arrival) rather than the fetch that follows it.
 * The fetch is driven by the same value, and its mapping to the API's `range`
 * parameter is covered in `src/lib/historyFilters.test.ts`.
 */
function renderAt(state?: Record<string, unknown>): string {
  return stripReactComments(
    renderToString(
      <MemoryRouter initialEntries={[{ pathname: "/app/history", state }]}>
        <HistoryPage />
      </MemoryRouter>,
    ),
  );
}

/** Minimal localStorage stand-in; the Vitest environment is `node`. */
function stubStorage(): void {
  const map = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
    },
    configurable: true,
    writable: true,
  });
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

describe("HistoryPage timeline window", () => {
  beforeEach(stubStorage);

  it("opens on the last 7 days when a link asks for it", () => {
    // Dashboard's "View all" points at 7 days, because the card it sits on lists
    // recent conversions.
    const html = renderAt(withTimeline("7d"));
    expect(html).toContain("Last 7 days");
  });

  it("starts on all time when nothing asks for a window", () => {
    expect(renderAt()).toContain("All time");
  });

  it("falls back to the stored preference", () => {
    localStorage.setItem("historyPageTimeline", "30d");
    expect(renderAt()).toContain("Last 30 days");
  });

  it("lets an explicit request win over the stored preference", () => {
    localStorage.setItem("historyPageTimeline", "30d");
    const html = renderAt(withTimeline("24h"));
    expect(html).toContain("Last 24 hours");
    expect(html).not.toContain("Last 30 days");
  });

  it("opens on all time when the navigation asks for the full window", () => {
    // The sidebar / phone-bar History entry always asks for "all", so a stored
    // window must not survive that click.
    localStorage.setItem("historyPageTimeline", "7d");
    const html = renderAt(withTimeline("all"));
    expect(html).toContain("All time");
    expect(html).not.toContain("Last 7 days");
  });

  it("ignores an invalid window in router state", () => {
    localStorage.setItem("historyPageTimeline", "30d");
    // A hand-typed URL or a stale history.state must not reach the API.
    expect(renderAt({ timeline: "90d" })).toContain("Last 30 days");
  });

  it("exposes the window control to assistive tech", () => {
    // The option list only renders once the popover is open, so the closed
    // trigger is all a server render can show — assert the control is present
    // and labelled, so a refactor cannot quietly drop the filter.
    const html = renderAt();
    expect(html).toContain("Filter by time range");
  });
});
