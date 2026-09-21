/**
 * Column templates for the app's data tables (Queue, History, Dashboard).
 *
 * WHY THIS MODULE EXISTS
 * The header row and every data row are SEPARATE CSS grid containers. A grid
 * `auto` track is resolved from its own container's content, so an `auto`
 * column makes the header and the rows disagree about that column's width —
 * and therefore about the position of every column before it. Before this was
 * fixed, at 1440px the Status column started at x=833 in the header but x=871
 * in the rows, and Progress at x=1014 vs x=1064 (the header's "Created" track
 * was 58px wide, sized by the word, while a row's was sized by the date or by a
 * per-row-varying number of action buttons).
 *
 * THE RULES (asserted by `tableColumns.test.ts`):
 *   1. No content-sized tracks (`auto`, `min-content`, `max-content`) — every
 *      track is `fr`, a fixed length, or `minmax(0, Npx)` so it can shrink on a
 *      narrow screen but never sizes itself from content.
 *   2. Wherever the header is visible, the rows use the SAME template — so the
 *      header and the rows always resolve identical column widths.
 *
 * Keep these as complete literal class strings: Tailwind's scanner reads them
 * from this file, so composing them at runtime (`"sm:" + X`) would leave the
 * utilities ungenerated.
 */

/** Queue: File · Format · Status · Progress · Created. The Created column only
 *  exists from `lg` (the date is hidden below it), so the track is added there
 *  rather than reserved empty at `sm`/`md`. */
export const QUEUE_HEADER_GRID =
  "hidden grid-cols-[2fr_1fr_1fr_140px] gap-4 border-b border-outline bg-surface-variant/40 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted sm:grid lg:grid-cols-[2fr_1fr_1fr_140px_150px]";

/** Phones show File + Status only, so the base template has two tracks. From
 *  `sm` up it matches `QUEUE_HEADER_GRID` exactly. */
export const QUEUE_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_minmax(0,110px)] items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50 sm:grid-cols-[2fr_1fr_1fr_140px] lg:grid-cols-[2fr_1fr_1fr_140px_150px]";

/** History: File · Format · Status · Created · Actions. */
export const HISTORY_HEADER_GRID =
  "hidden grid-cols-[2fr_1fr_1fr_170px] gap-4 border-b border-outline bg-surface-variant/40 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted md:grid lg:grid-cols-[2fr_1fr_1fr_140px_170px]";

/** Phones show File + Status + Actions; `md` drops Actions into its own track
 *  and matches the header; `lg` adds the Created column in front of it.
 *
 *  Actions are always the LAST track and everything inside is right-aligned, so
 *  a row with fewer buttons (or none) still lines up on the column's edge. */
export const HISTORY_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_minmax(0,110px)_minmax(0,150px)] items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50 md:grid-cols-[2fr_1fr_1fr_170px] lg:grid-cols-[2fr_1fr_1fr_140px_170px]";

/** Dashboard recent conversions: File · Format · Status · Created · Action.
 *  There is no header row here, but the tracks are fixed for the same reason:
 *  `FormatMorph` (which prints the format name beside its tile) and the status
 *  pill are different widths per row, so content-sized tracks made the date and
 *  action columns jump from row to row. The action track is always present, so
 *  a row with a download button does not push the date left. */
export const DASHBOARD_ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_minmax(0,140px)_minmax(0,110px)_minmax(0,40px)] items-center gap-4 px-5 py-3 transition-colors hover:bg-surface-variant/50 sm:grid-cols-[minmax(0,1fr)_minmax(0,150px)_minmax(0,110px)_130px_40px]";

/** Breakpoints in ascending order, for comparing templates across them. */
export const BREAKPOINTS = ["sm", "md", "lg", "xl", "2xl"] as const;

export interface ParsedGridTemplate {
  /** Breakpoint prefix, or "" for the base (unprefixed) template. */
  breakpoint: string;
  /** Track list, e.g. "2fr_1fr_1fr_140px". */
  tracks: string;
}

/** Extract every `[<bp>:]grid-cols-[...]` utility from a class string. */
export function parseGridTemplates(className: string): ParsedGridTemplate[] {
  const out: ParsedGridTemplate[] = [];
  for (const match of className.matchAll(/(?:(sm|md|lg|xl|2xl):)?grid-cols-\[([^\]]*)\]/g)) {
    out.push({ breakpoint: match[1] ?? "", tracks: match[2] });
  }
  return out;
}

/** The tracks in effect at a given breakpoint, carrying earlier declarations
 *  forward exactly as the cascade does (later, narrower declarations win). */
export function tracksAt(
  templates: ParsedGridTemplate[],
  breakpoint: string,
): string | undefined {
  const order: string[] = ["", ...BREAKPOINTS];
  const limit = order.indexOf(breakpoint);
  if (limit === -1) return undefined;

  let tracks: string | undefined;
  let applied = -1;
  for (const template of templates) {
    const index = order.indexOf(template.breakpoint);
    if (index === -1 || index > limit || index < applied) continue;
    tracks = template.tracks;
    applied = index;
  }
  return tracks;
}
