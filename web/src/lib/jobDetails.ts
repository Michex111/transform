/**
 * What a job row shows once it is expanded.
 *
 * Pure and DOM-free so the rules are unit-testable: the panel is the only place
 * a user can see the byte sizes, the token cost, the conversion time or why a
 * job failed, and "which rows are entitled to which field" is exactly the kind
 * of rule that silently drifts when it lives in markup.
 *
 * The list is ordered for reading, not by importance of the underlying field:
 * conversion → progress → output → sizes → encryption → cost → time → error.
 */

import { formatDateTimeOrNull, formatDuration } from "@/lib/format";
import {
  isActiveJob,
  jobCreatedAt,
  jobProgress,
  showsCreditsUsed,
  type UiJob,
} from "@/jobs/jobStore";

/**
 * A single label/value line in the expanded panel.
 *
 * `kind` — not `key` — is what the renderer switches on: two rows can share a
 * kind (Input size and Output size are both sizes), and every row's `key` is
 * unique and stable so React reconciles the list and tests can find a row by
 * name.
 */
export type JobDetail =
  /** Source → target, rendered as the brand's morph so it matches the tables. */
  | { key: "conversion"; label: string; kind: "conversion"; from: string; to: string }
  /** Live percentage for a job still running. */
  | { key: "progress"; label: string; kind: "progress"; percent: number | null }
  /** Name of the file the conversion produced. */
  | { key: "output"; label: string; kind: "text"; value: string }
  /** Bytes moved, rendered by `formatBytes`. */
  | { key: "input-size"; label: string; kind: "size"; bytes: number }
  | { key: "output-size"; label: string; kind: "size"; bytes: number }
  /** Client-side encryption, shown only when it was actually used. */
  | { key: "encrypted"; label: string; kind: "encrypted"; value: string }
  /** Tokens charged. Only a successful conversion is billed. */
  | { key: "tokens"; label: string; kind: "tokens"; credits: number }
  | { key: "duration"; label: string; kind: "text"; value: string }
  | { key: "created"; label: string; kind: "text"; value: string }
  | { key: "error"; label: string; kind: "error"; message: string };

/**
 * The file name a completed job produced, from its stored object key.
 *
 * The key is namespaced by user and job (`outputs/user/7/job/abc/report.docx`),
 * so only the last segment is worth showing. It is worth showing at all because
 * a converter may choose its own container extension — a multi-page `pdf → png`
 * job yields a `.zip` — and then the produced name is the only place that is
 * visible.
 */
function outputFileName(outputFile: string | null | undefined): string | null {
  if (!outputFile) return null;
  const name = outputFile.split("/").pop()?.trim();
  return name ? name : null;
}

/**
 * The detail rows for one job, omitting anything that has no real value.
 *
 * Nothing here renders a placeholder: a field whose value the backend has not
 * reported is left out rather than printed as "—", "0" or "0 B". An expanded
 * panel is a promise that opening the row tells you something new, and a column
 * of placeholders breaks that promise more loudly than a shorter list does.
 * That is also why `0` sizes are treated as absent — the worker writes 0 when it
 * never measured the file, which is not the same as an empty file.
 */
export function jobDetails(job: UiJob): JobDetail[] {
  const details: JobDetail[] = [
    {
      key: "conversion",
      label: "Conversion",
      kind: "conversion",
      from: job.source_format,
      to: job.target_format,
    },
  ];

  // A running job has no cost, size or duration yet; what it does have is
  // progress.
  if (isActiveJob(job)) {
    details.push({
      key: "progress",
      label: "Progress",
      kind: "progress",
      percent: jobProgress(job),
    });
  }

  // Only a finished job has an output to name.
  const output = job.status === "COMPLETED" ? outputFileName(job.output_file) : null;
  if (output) details.push({ key: "output", label: "Output file", kind: "text", value: output });

  // Sizes sit adjacent so the two are easy to compare. Both are independently
  // omittable: an input size with no output size is still useful.
  if ((job.input_size_bytes ?? 0) > 0) {
    details.push({
      key: "input-size",
      label: "Input size",
      kind: "size",
      bytes: job.input_size_bytes ?? 0,
    });
  }
  if ((job.output_size_bytes ?? 0) > 0) {
    details.push({
      key: "output-size",
      label: "Output size",
      kind: "size",
      bytes: job.output_size_bytes ?? 0,
    });
  }

  // Worth stating only when it is true: "not encrypted" is the default, and
  // saying so on every row would be noise.
  if (job.client_encrypted) {
    details.push({
      key: "encrypted",
      label: "Encryption",
      kind: "encrypted",
      value: "End-to-end",
    });
  }

  if (showsCreditsUsed(job)) {
    details.push({
      key: "tokens",
      label: "Tokens used",
      kind: "tokens",
      credits: job.credits_used ?? 0,
    });
  }

  const duration = formatDuration(job.compute_duration_ms);
  if (duration) details.push({ key: "duration", label: "Duration", kind: "text", value: duration });

  // The server's `created_at` covers rows restored from history; the
  // client-side `createdAt` covers jobs started in this browser, which may be
  // too new to appear in a history response yet. A row with neither has no date
  // to show.
  const created = formatDateTimeOrNull(jobCreatedAt(job));
  if (created) details.push({ key: "created", label: "Converted", kind: "text", value: created });

  // The error is spelled out here rather than left to the row's hover-only
  // tooltip, which a touch device cannot open at all.
  if (job.status === "FAILED" && job.errorMessage) {
    details.push({ key: "error", label: "Error", kind: "error", message: job.errorMessage });
  }

  return details;
}
