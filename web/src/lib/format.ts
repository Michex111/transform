// Format + job status vocabulary and styling helpers.

export interface FormatMeta {
  label: string;
  color: string;
}

const FORMATS: Record<string, FormatMeta> = {
  pdf: { label: "PDF", color: "var(--color-fmt-pdf)" },
  docx: { label: "DOCX", color: "var(--color-fmt-word)" },
  doc: { label: "DOC", color: "var(--color-fmt-word)" },
  xlsx: { label: "XLSX", color: "var(--color-fmt-excel)" },
  xls: { label: "XLS", color: "var(--color-fmt-excel)" },
  csv: { label: "CSV", color: "var(--color-fmt-excel)" },
  png: { label: "PNG", color: "var(--color-fmt-image)" },
  jpg: { label: "JPG", color: "var(--color-fmt-image)" },
  jpeg: { label: "JPEG", color: "var(--color-fmt-image)" },
  webp: { label: "WEBP", color: "var(--color-fmt-image)" },
  svg: { label: "SVG", color: "var(--color-fmt-image)" },
  mp3: { label: "MP3", color: "var(--color-fmt-audio)" },
  wav: { label: "WAV", color: "var(--color-fmt-audio)" },
  ogg: { label: "OGG", color: "var(--color-fmt-audio)" },
  mp4: { label: "MP4", color: "var(--color-fmt-video)" },
  webm: { label: "WEBM", color: "var(--color-fmt-video)" },
  txt: { label: "TXT", color: "var(--color-fmt-text)" },
  md: { label: "MD", color: "var(--color-fmt-text)" },
};

export function formatMeta(fmt: string): FormatMeta {
  const key = fmt.toLowerCase().trim();
  return FORMATS[key] ?? {
    label: key.toUpperCase(),
    color: "var(--color-fmt-text)",
  };
}

/**
 * The lowercased extension segment of a file name, `""` when there is none
 * (`"Report.PDF"` → `"pdf"`, `"README"` → `"readme"`, `""` → `""`).
 *
 * The single parsing helper for "what does the user's file claim to be". It is
 * deliberately NOT `formatExt`: that function falls back to the MIME type and
 * then to `"txt"`, which would silently reclassify an extensionless or
 * long-extension file — wrong for the callers that validate the user's own file
 * against the supported conversion graph.
 */
export function fileNameExtension(fileName: string): string {
  return fileName.split(".").pop()?.toLowerCase() ?? "";
}

/** Derive a file extension from a file name (or mime type fallback). */
export function formatExt(fileName: string, mimeType?: string): string {
  const fromName = fileName.split(".").pop()?.toLowerCase().trim();
  if (fromName && fromName.length <= 5) return fromName;
  if (mimeType) {
    const fromMime = mimeType.split("/").pop()?.toLowerCase().trim();
    if (fromMime) return fromMime === "jpeg" ? "jpg" : fromMime;
  }
  return "txt";
}

export type JobStatus = "AWAITING_UPLOAD" | "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";

export interface StatusMeta {
  label: string;
  color: string;
  pulse: boolean;
}

const STATUS: Record<string, StatusMeta> = {
  AWAITING_UPLOAD: { label: "Waiting for file", color: "var(--color-muted)", pulse: false },
  PENDING: { label: "Queued", color: "var(--color-warning)", pulse: true },
  PROCESSING: { label: "Converting", color: "var(--color-primary)", pulse: true },
  COMPLETED: { label: "Ready", color: "var(--color-success)", pulse: false },
  FAILED: { label: "Failed", color: "var(--color-error)", pulse: false },
};

export function statusMeta(status: string): StatusMeta {
  return STATUS[status.toUpperCase()] ?? { label: status, color: "var(--color-muted)", pulse: false };
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * How long a conversion's converter took, or `null` when there is nothing to
 * report.
 *
 * Returns `null` rather than a placeholder for a missing value because the
 * caller's job is to omit the row: `compute_duration_ms` is `0` for a job that
 * has not run yet (and for every job recorded before the field existed), and a
 * literal "0 ms" would state as fact something the server never reported.
 *
 * Sub-second times keep millisecond resolution (a 240 ms conversion done in
 * "0.2 s" hides the difference that matters); seconds keep one decimal below
 * 10 for the same reason, and minutes roll up so a long job reads as a single
 * value instead of a four-digit seconds count.
 */
export function formatDuration(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms <= 0) return null;

  // Round to the smallest unit *first*, then choose the unit from the rounded
  // value. Deciding from the raw value produces off-by-one readings like
  // "999.6" → "1000 ms" and "59.999 s" → "60 s", which read as bugs.
  const roundedMs = Math.round(ms);
  if (roundedMs < 1000) return `${roundedMs} ms`;

  const totalSeconds = roundedMs / 1000;
  // One decimal up to 9.9s (240ms vs 1.2s is a real difference to a user
  // watching a spinner), whole seconds above that.
  if (totalSeconds < 9.95) return `${totalSeconds.toFixed(1)} s`;
  if (totalSeconds < 59.5) return `${Math.round(totalSeconds)} s`;

  const minutes = Math.floor(totalSeconds / 60);
  const remainder = Math.round(totalSeconds - minutes * 60);
  // 59.9s rounds up to a whole minute here, so carry it instead of printing
  // "0 m 60 s".
  if (remainder === 60) return `${minutes + 1} m`;
  return remainder ? `${minutes} m ${remainder} s` : `${minutes} m`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Timestamp for an instant, or `null` when it is absent or unparseable.
 *
 * The `formatDateTime` counterpart to `formatDateOrNull`: the job rows carry a
 * client-side `createdAt` that is simply missing for anything listed from the
 * server without one, and a detail row reading "—" is worse than no row.
 */
export function formatDateTimeOrNull(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return formatDateTime(iso);
}

/**
 * Local calendar date for an instant, or `null` when it is absent or
 * unparseable.
 *
 * Unlike `formatDate`, which renders an em dash placeholder, this lets a caller
 * omit a row entirely rather than print a placeholder for missing data.
 *
 * Formats in the **viewer's** timezone deliberately: a credit reset happens at
 * midnight UTC, which is the previous evening for anyone west of UTC, so the
 * correct local day only falls out of formatting the instant — not the UTC
 * date.
 */
export function formatDateOrNull(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
