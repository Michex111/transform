// The pure rules behind the conversion-result card.
//
// They live here rather than beside the component because the card's whole
// point is that the file the user handed over and the file the worker produced
// are never confused — and that invariant is invisible in a screenshot. A card
// labelled with the source's name looks exactly as plausible as one labelled
// correctly, so the rule is pinned by unit tests instead.

import type { AssistantArtifact } from "@/api/types";
import type { UiJob } from "@/jobs/jobStore";

export interface ConversionIdentity {
  /** Display name for the card and its accessible group label. */
  name: string;
  /** The file that was handed over, preferring the job's authoritative field. */
  sourceName: string;
  /** The file the worker produced, or `null` while there is not one yet. */
  outputName: string | null;
  /** What the Download/Save actions act on (the output once it exists). */
  actionName: string;
}

/**
 * The four names a conversion card shows, resolved from the artifact and the
 * fetched job.
 *
 * The wire format is the reason this is not a one-liner:
 *
 *   - `input_file` is a plain display name (`"upei residence.pdf"`);
 *   - `output_file` is the stored **object key**
 *     (`"output/user/49/job/<id>/upei residence.docx"`), not a filename.
 *
 * Both go through the same basename reduction. For `input_file` that is a no-op,
 * and for `output_file` it is the difference between showing the user their
 * document's name and showing them the storage layout of the bucket.
 *
 * `outputName` is *derived from* the authoritative key rather than invented: it
 * is still only ever the worker's own output path, read from the job. It is
 * never guessed from the source — not by swapping the extension, not by
 * appending a suffix — and stays `null` while the worker has reported nothing,
 * so the card says the result is pending instead of naming a file that does not
 * exist.
 */
export function conversionIdentity(
  artifact: AssistantArtifact,
  job: Pick<UiJob, "input_file" | "output_file"> | null,
): ConversionIdentity {
  // `artifact.name` is the display name the API chose (the backend sets it to
  // the job's `input_file`); the job row is the record of what actually ran, so
  // it wins when it is present.
  const displaySource = fileNameFromKey(job?.input_file || artifact.name);
  const outputName = fileNameFromKey(job?.output_file);
  return {
    name: displaySource ?? "Conversion",
    sourceName: displaySource ?? "the original file",
    outputName,
    actionName: outputName ?? displaySource ?? "Conversion",
  };
}

/**
 * The last path segment of a file name or object key, or `null` when there is
 * nothing to show.
 *
 * The API sends `output_file: ""` — not `null` — for a job with no output yet,
 * so a blank value has to be normalised away; `??` alone does not catch it and
 * the card would render an empty filename where it means to say "pending".
 */
function fileNameFromKey(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  if (!trimmed) return null;
  const segment = trimmed.split("/").pop()?.trim();
  return segment ? segment : null;
}

/**
 * The conversion card header's wording, derived from the job's own status.
 *
 * A status this bundle does not recognise (an API newer than the SPA — the two
 * deploy independently) falls through to the neutral "Conversion" rather than
 * inventing a phase.
 */
export function conversionHeadline(status: string | null): string {
  switch (status?.toUpperCase()) {
    case "COMPLETED":
      return "Conversion complete";
    case "FAILED":
      return "Conversion failed";
    case "PROCESSING":
      return "Converting";
    case "PENDING":
    case "AWAITING_UPLOAD":
      return "Conversion queued";
    default:
      return "Conversion";
  }
}
