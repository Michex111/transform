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
