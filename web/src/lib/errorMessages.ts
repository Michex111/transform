/**
 * Map raw backend/worker error messages to safe, human-readable text.
 *
 * The converter worker can raise messages that embed server-internal details
 * such as temporary file paths (e.g. "Converter produced no output file at
 * /tmp/tmpXXXX/notes.docx for job 123"). Those must never reach the browser.
 * This normalizes known worker failure modes to friendly copy while retaining
 * the job's correlation id (as a reference, never a path) for support triage.
 */

// A server-internal path we must never leak: POSIX /tmp, Windows %TEMP%, or a
// python `Path(...)` repr.
const tempPathPattern =
  /(?:\/tmp\/|[a-zA-Z]:\\temp\\)[^\s"'`)]*|\bPath\s*\(\s*['"][^'"]+['"]\s*\)/gi;

const jobRefPattern = /(?:job|job_id|job id)(?:\s*[:=#]?\s*)([a-zA-Z0-9_-]+)/i;

function referenceHint(raw: string): string | null {
  const m = jobRefPattern.exec(raw);
  return m?.[1] ?? null;
}

// Append a correlation id so support can trace the server event, never a path.
function withRef(text: string, ref: string | null): string {
  return ref ? `${text} (Job ref: ${ref})` : text;
}

export function friendlyErrorMessage(raw?: string | null): string {
  if (!raw) return "Conversion failed";
  const msg = raw.trim();
  if (!msg) return "Conversion failed";

  const ref = referenceHint(msg);

  // Known worker failure modes → friendly copy (retaining the job ref).
  if (/converter produced no output file/i.test(msg)) {
    return withRef(
      "The conversion produced no output file. The uploaded file may not match the selected source format — check the file and try again.",
      ref,
    );
  }
  if (/no converter found for conversion type/i.test(msg)) {
    return withRef("This conversion is not supported.", ref);
  }
  if (/credits exhausted/i.test(msg)) {
    return withRef(
      "You've run out of conversion credits. Upgrade your plan or purchase more credits.",
      ref,
    );
  }
  if (/\b(failed|could not)\s+to\s+(download|upload)\b/i.test(msg)) {
    return withRef("The file transfer failed. Please try again.", ref);
  }

  // Strip any remaining internal temp/file paths before surfacing.
  const safe = msg.replace(tempPathPattern, "[file]").replace(/["'`]/g, "").trim();

  // Keep a correlation id so support can trace the server event, never a path.
  return withRef(safe || "Conversion failed", ref);
}
