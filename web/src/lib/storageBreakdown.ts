// Presentation model for the dashboard's storage breakdown.
//
// The API reports storage usage per *file extension*; the UI shows it per
// *category*. This module is the single place that folds the raw extensions
// into the categories the bar can distinguish, reusing `formatVisual` so the
// taxonomy stays in sync with the format picker and the rest of the app.

import { formatVisual, type FormatCategory } from "@/lib/formatVisual";
import type { StorageBreakdownEntry } from "@/api/types";

/** The categories that get their own colour on the storage bar. `other`
 *  collects every `formatVisual` category that has no dedicated token. */
export type StorageCategory =
  | "document"
  | "spreadsheet"
  | "presentation"
  | "image"
  | "audio"
  | "video"
  | "archive"
  | "other";

/** Only existing design tokens — deliberately coarse so adjacent segments
 *  never collide on the same colour. */
export const STORAGE_CATEGORY_COLOR: Record<StorageCategory, string> = {
  document: "var(--color-fmt-pdf)",
  spreadsheet: "var(--color-fmt-excel)",
  presentation: "var(--color-fmt-word)",
  image: "var(--color-fmt-image)",
  audio: "var(--color-fmt-audio)",
  video: "var(--color-fmt-video)",
  archive: "var(--color-warning)",
  other: "var(--color-fmt-text)",
};

export const STORAGE_CATEGORY_LABEL: Record<StorageCategory, string> = {
  document: "Documents",
  spreadsheet: "Spreadsheets",
  presentation: "Presentations",
  image: "Images",
  audio: "Audio",
  video: "Video",
  archive: "Archives",
  other: "Other",
};

/** `formatVisual` category → storage bucket. Everything without its own token
 *  (ebook, font, cad, code, text, unknown) collapses into `other`. */
const BUCKET: Record<FormatCategory, StorageCategory> = {
  document: "document",
  spreadsheet: "spreadsheet",
  presentation: "presentation",
  image: "image",
  audio: "audio",
  video: "video",
  archive: "archive",
  ebook: "other",
  font: "other",
  cad: "other",
  code: "other",
  text: "other",
};

export interface StorageBreakdownSegment {
  category: StorageCategory;
  /** Human name, e.g. "Documents". */
  label: string;
  /** CSS colour token, e.g. "var(--color-fmt-pdf)". */
  color: string;
  bytes: number;
  fileCount: number;
  /**
   * The category's share of the storage the user has already used, or `null`
   * when `usedBytes` is 0 (there is nothing to take a percentage of — never
   * render `NaN`).
   */
  percent: number | null;
}

/**
 * Fold per-extension usage into category segments, largest first.
 *
 * `usedBytes` is the denominator for the displayed percentage: the natural
 * reading of "the percent of storage this category occupies".
 */
export function aggregateStorageBreakdown(
  breakdown: StorageBreakdownEntry[] | null | undefined,
  usedBytes: number,
): StorageBreakdownSegment[] {
  const totals = new Map<StorageCategory, { bytes: number; fileCount: number }>();

  for (const entry of breakdown ?? []) {
    const bytes = Number(entry?.bytes);
    // Defensive: the contract promises `bytes > 0`, but never let bad data
    // create an invisible or negative segment.
    if (!Number.isFinite(bytes) || bytes <= 0) continue;

    const bucket = BUCKET[formatVisual(entry.extension ?? "").category] ?? "other";
    const acc = totals.get(bucket) ?? { bytes: 0, fileCount: 0 };
    acc.bytes += bytes;
    const count = Number(entry.file_count);
    if (Number.isFinite(count) && count > 0) acc.fileCount += count;
    totals.set(bucket, acc);
  }

  const base = Number.isFinite(usedBytes) && usedBytes > 0 ? usedBytes : null;

  return [...totals.entries()]
    .map(([category, value]) => ({
      category,
      label: STORAGE_CATEGORY_LABEL[category],
      color: STORAGE_CATEGORY_COLOR[category],
      bytes: value.bytes,
      fileCount: value.fileCount,
      percent: base === null ? null : (value.bytes / base) * 100,
    }))
    .sort((a, b) => b.bytes - a.bytes);
}

/** Compact percentage for the tooltip/legend: `56%`, `3.4%`, `0.41%`.
 *  Returns `null` when there is no meaningful percentage to show. */
export function formatStoragePercent(percent: number | null): string | null {
  if (percent === null || !Number.isFinite(percent) || percent <= 0) return null;
  if (percent >= 10) return `${Math.round(percent)}%`;
  if (percent >= 1) return `${percent.toFixed(1)}%`;
  if (percent >= 0.01) return `${percent.toFixed(2)}%`;
  return "<0.01%";
}
