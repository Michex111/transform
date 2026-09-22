// Tests for the rules that decide what an expanded job row shows.
//
// These are the rules a user depends on, because the panel is the only place
// the byte sizes, the exact conversion time or the failure reason can be seen —
// at any width, since the row has no column for them. The pattern being pinned
// is "omit rather than placeholder": a field the backend never reported must not
// appear at all, so an expansion always tells the user something new.

import { describe, expect, it } from "vitest";
import { jobDetails } from "@/lib/jobDetails";
import type { UiJob } from "@/jobs/jobStore";

function job(overrides: Partial<UiJob> = {}): UiJob {
  return {
    job_id: "job-1",
    status: "COMPLETED",
    source_format: "docx",
    target_format: "pdf",
    input_file: "contract.docx",
    output_file: "outputs/user/7/job/abc/contract.pdf",
    object_key: "uploads/contract.docx",
    download_url: "https://example.invalid/contract.pdf",
    ...overrides,
  };
}

/** The detail keys, in render order. */
function keys(j: UiJob): string[] {
  return jobDetails(j).map((d) => d.key);
}

describe("jobDetails", () => {
  it("always leads with the conversion, as the brand's morph", () => {
    const [first] = jobDetails(job());
    expect(first).toEqual({
      key: "conversion",
      label: "Conversion",
      kind: "conversion",
      from: "docx",
      to: "pdf",
    });
  });

  it("reports the output, sizes, cost, duration and time of a finished conversion", () => {
    const details = jobDetails(
      job({
        credits_used: 12,
        compute_duration_ms: 1234,
        input_size_bytes: 2_400_000,
        output_size_bytes: 812_000,
        createdAt: "2026-09-21T13:45:00Z",
      }),
    );
    expect(details.map((d) => d.key)).toEqual([
      "conversion",
      "output",
      "input-size",
      "output-size",
      "tokens",
      "duration",
      "created",
    ]);
    // Only the file name is shown; the storage key is namespaced by user and job
    // and means nothing to the reader.
    expect(details[1]).toEqual({
      key: "output",
      label: "Output file",
      kind: "text",
      value: "contract.pdf",
    });
    expect(details[2]).toEqual({
      key: "input-size",
      label: "Input size",
      kind: "size",
      bytes: 2_400_000,
    });
    expect(details[3]).toEqual({
      key: "output-size",
      label: "Output size",
      kind: "size",
      bytes: 812_000,
    });
    expect(details[4]).toEqual({ key: "tokens", label: "Tokens used", kind: "tokens", credits: 12 });
    expect(details[5]).toEqual({ key: "duration", label: "Duration", kind: "text", value: "1.2 s" });
    expect(details[6]).toMatchObject({ key: "created", label: "Converted", kind: "text" });
  });

  it("omits a size the worker never measured", () => {
    // The worker writes 0 when it could not stat a file, and every row created
    // before these columns existed is also 0. Rendering "0 B" would state as
    // fact something nobody observed.
    const none = keys(job());
    expect(none).not.toContain("input-size");
    expect(none).not.toContain("output-size");

    // Each size stands on its own: an input size with no output size is still
    // worth showing.
    const inputOnly = keys(job({ input_size_bytes: 500 }));
    expect(inputOnly).toContain("input-size");
    expect(inputOnly).not.toContain("output-size");
  });

  it("names the output only once the job has produced one", () => {
    expect(keys(job({ output_file: null }))).not.toContain("output");
    expect(keys(job({ status: "PROCESSING", output_file: "x/y.pdf" }))).not.toContain("output");
    // A truncated/odd key with no name segment has nothing to show.
    expect(keys(job({ output_file: "outputs/user/7/" }))).not.toContain("output");
  });

  it("mentions encryption only when the file actually was encrypted", () => {
    expect(keys(job({ client_encrypted: true }))).toContain("encrypted");
    expect(jobDetails(job({ client_encrypted: true }))).toContainEqual({
      key: "encrypted",
      label: "Encryption",
      kind: "encrypted",
      value: "End-to-end",
    });
    // "Not encrypted" is the default; saying it on every row would be noise.
    expect(keys(job({ client_encrypted: false }))).not.toContain("encrypted");
    expect(keys(job())).not.toContain("encrypted");
  });

  it("omits the cost of a completed conversion that used none", () => {
    // `showsCreditsUsed` owns this rule and is shared with the desktop row, so
    // the two cannot drift; a zero-cost conversion shows no token row anywhere.
    expect(keys(job({ credits_used: 0 }))).not.toContain("tokens");
  });

  it("never charges a failed conversion", () => {
    const details = jobDetails(
      job({ status: "FAILED", credits_used: 9, errorMessage: "converter crashed" }),
    );
    expect(details.map((d) => d.key)).not.toContain("tokens");
    expect(details).toContainEqual({
      key: "error",
      label: "Error",
      kind: "error",
      message: "converter crashed",
    });
  });

  it("spells the failure out rather than leaving it to the hover-only tooltip", () => {
    // The row shows the message through a popover; a touch device cannot open
    // one, so the panel has to carry the text.
    expect(keys(job({ status: "FAILED", errorMessage: "converter crashed" }))).toContain("error");
    // A failed row the worker gave no message for has nothing to say, so it gets
    // no empty "Error" row either.
    expect(keys(job({ status: "FAILED", errorMessage: undefined }))).not.toContain("error");
  });

  it("shows progress, not cost, size or duration, for a job still running", () => {
    const details = jobDetails(
      job({
        status: "PROCESSING",
        progress: 30,
        compute_duration_ms: 0,
        credits_used: 0,
        input_size_bytes: 0,
        output_size_bytes: 0,
      }),
    );
    expect(details.map((d) => d.key)).toEqual(["conversion", "progress"]);
    expect(details[1]).toEqual({
      key: "progress",
      label: "Progress",
      kind: "progress",
      percent: 30,
    });
  });

  it("passes an unreported progress through as null, so the bar stays indeterminate", () => {
    const details = jobDetails(job({ status: "PENDING", progress: undefined }));
    expect(details).toContainEqual({
      key: "progress",
      label: "Progress",
      kind: "progress",
      percent: null,
    });
  });

  it("omits the time for a row that never carried a date", () => {
    // `createdAt` is set by the client, so anything listed from the server's
    // history without one has no date to show — and the panel must not print a
    // placeholder for it.
    expect(keys(job({ createdAt: undefined }))).not.toContain("created");
  });

  it("uses the server's creation timestamp for a row restored from history", () => {
    // A history row has no client-side `createdAt` at all; without falling back
    // to the API's `created_at` the "Converted" line — an explicit requirement
    // of the expanded panel — could never appear.
    const details = jobDetails(
      job({ createdAt: undefined, created_at: "2026-09-21T13:45:00Z" }),
    );
    expect(details).toContainEqual({
      key: "created",
      label: "Converted",
      kind: "text",
      value: expect.stringContaining(String(new Date("2026-09-21T13:45:00Z").getDate())),
    });
  });

  it("omits the duration for a job the worker never timed", () => {
    expect(keys(job({ compute_duration_ms: 0 }))).not.toContain("duration");
    expect(keys(job({ compute_duration_ms: undefined }))).not.toContain("duration");
  });
});
