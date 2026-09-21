// FormatThumb — a small, deliberately designed "document silhouette" that
// stands in for a file format everywhere in the product UI (tables, cards,
// hero, nav wordmark, page loader).
//
// Pure presentational + stateless so it is safe in long lists, and sized in
// whole pixels so it never shifts surrounding layout.

import type { CSSProperties } from "react";
import { formatTint, formatVisual } from "@/lib/formatVisual";

export type FormatThumbSize = "xs" | "sm" | "md" | "lg" | "xl";

interface FormatThumbProps {
  /** Extension or label hint (case-insensitive), e.g. "pdf", ".PNG". */
  format: string;
  size?: FormatThumbSize;
  /**
   * Accessible name override. Defaults to the format's canonical label
   * (e.g. "PDF"). Pass `""` to make the tile purely decorative — use this when
   * the format name is already rendered next to it.
   */
  label?: string;
  /** Render the extension badge inside the tile (auto-hidden on tiny sizes). */
  showLabel?: boolean;
  className?: string;
  /** Native tooltip. Defaults to the format label on sizes without a badge. */
  title?: string;
}

const DIM: Record<FormatThumbSize, number> = { xs: 20, sm: 28, md: 36, lg: 52, xl: 72 };
const GLYPH: Record<FormatThumbSize, number> = { xs: 12, sm: 16, md: 20, lg: 26, xl: 34 };
const RADIUS: Record<FormatThumbSize, number> = { xs: 4, sm: 6, md: 8, lg: 11, xl: 15 };
const FOLD: Record<FormatThumbSize, number> = { xs: 6, sm: 8, md: 11, lg: 15, xl: 21 };

/** Badge metrics per size — `null` means the tile is too small for text. */
const BADGE: Record<FormatThumbSize, { h: number; font: number; pad: number } | null> = {
  xs: null,
  sm: null,
  md: { h: 15, font: 8, pad: 3 },
  lg: { h: 19, font: 10, pad: 5 },
  xl: { h: 25, font: 12, pad: 7 },
};

export function FormatThumb({
  format,
  size = "md",
  label,
  showLabel = true,
  className = "",
  title,
}: FormatThumbProps) {
  const { color, label: formatLabel, Icon } = formatVisual(format);
  const box = DIM[size];
  const badge = showLabel ? BADGE[size] : null;
  const accessibleName = label ?? formatLabel;
  // `label=""` opts out of the accessible tree entirely.
  const decorative = label === "";
  // Without real DOM text the tile has to name itself.
  const namesItself = !decorative && badge === null;

  const pageStyle: CSSProperties = {
    width: box,
    height: box,
    boxSizing: "border-box",
    borderRadius: RADIUS[size],
    background: `linear-gradient(160deg, ${formatTint(color, 18)}, ${formatTint(color, 7)})`,
    border: `1px solid ${formatTint(color, 27)}`,
    boxShadow: box >= DIM.md ? `0 2px 10px -4px ${formatTint(color, 50)}` : undefined,
    overflow: "hidden",
  };

  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={pageStyle}
      aria-hidden={decorative || undefined}
      title={title ?? (namesItself ? accessibleName : undefined)}
      role={namesItself ? "img" : undefined}
      aria-label={namesItself ? accessibleName : undefined}
    >
      {/* folded top-right corner */}
      <span
        aria-hidden
        className="absolute right-0 top-0"
        style={{
          width: FOLD[size],
          height: FOLD[size],
          background: formatTint(color, 22),
          clipPath: "polygon(100% 0, 0 0, 100% 100%)",
        }}
      />
      <Icon
        size={GLYPH[size]}
        weight="duotone"
        aria-hidden
        className="relative"
        style={{ color, marginBottom: badge ? badge.h : 0 }}
      />
      {badge && (
        <span className="absolute inset-x-0.5 bottom-0.5 flex justify-center">
          <span
            className="max-w-full truncate rounded-[3px] font-mono font-bold uppercase"
            style={{
              height: badge.h,
              lineHeight: `${badge.h - 2}px`,
              fontSize: badge.font,
              padding: `0 ${badge.pad}px`,
              // Lightened toward white: the raw format color on a 20% tint of
              // itself falls below WCAG AA for the darker hues (Word, Audio).
              color: formatTint(color, 70, "#ffffff"),
              background: formatTint(color, 20, "var(--color-surface)"),
              border: `1px solid ${formatTint(color, 27)}`,
            }}
          >
            {formatLabel}
          </span>
        </span>
      )}
    </span>
  );
}
