// FormatLink — the vocabulary for "a link that names a format" used by the
// format catalogue and the `/{ext}-converter` hub pages. Thumbnail + label so
// it matches the rest of the site's format language (FormatThumb).

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FormatThumb, type FormatThumbSize } from "@/components/FormatThumb";
import { formatVisual } from "@/lib/formatVisual";

interface FormatLinkProps {
  /** Destination, e.g. `/pdf-converter` or `/pdf-to-docx`. */
  to: string;
  /** Extension the thumbnail represents. The tile is decorative; the label names it. */
  format: string;
  /** Visible text. Defaults to the format's own uppercase label. */
  children?: ReactNode;
  size?: FormatThumbSize;
  className?: string;
}

export function FormatLink({
  to,
  format,
  children,
  size = "xs",
  className = "",
}: FormatLinkProps) {
  const { label } = formatVisual(format);
  return (
    <Link
      to={to}
      className={`inline-flex min-w-0 items-center gap-2 rounded-lg border border-outline bg-surface px-2.5 py-2 transition-colors hover:border-primary/40 hover:bg-surface-variant ${className}`}
    >
      <FormatThumb format={format} size={size} label="" />
      <span className="min-w-0 truncate text-xs font-medium text-on-background">
        {children ?? label}
      </span>
    </Link>
  );
}

/** The dense, scannable multi-column grid the format links are laid out in. */
export function FormatLinkGrid({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 ${className}`}>
      {children}
    </div>
  );
}
