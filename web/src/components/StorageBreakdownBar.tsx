import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { formatBytes } from "@/lib/format";
import {
  aggregateStorageBreakdown,
  formatStoragePercent,
  type StorageBreakdownSegment,
} from "@/lib/storageBreakdown";
import type { StorageBreakdownEntry } from "@/api/types";

export interface StorageBreakdownBarProps {
  /** Total bytes the user currently stores. */
  usedBytes: number;
  /** The account's storage quota in bytes (the track's meaning). */
  limitBytes: number;
  /** `used_bytes / limit_bytes` as a percentage, as reported by the API. */
  usedPercent: number;
  /**
   * Per-extension usage. Missing/`null`/empty means "no categorised data" and
   * renders nothing at all — the caller keeps the plain fallback bar.
   */
  breakdown?: StorageBreakdownEntry[] | null;
  className?: string;
}

/**
 * A storage bar whose full width is subdivided by file category.
 *
 * The bar shows the **composition of what is stored**: segments span the whole
 * track, each proportional to that category's share of the used bytes. Quota
 * consumption is reported as text under the bar. Subdividing only the *filled*
 * portion of a quota bar was the obvious alternative, but at realistic usage
 * (a dev account used 0.33%) that left every segment sub-pixel — the hover
 * interaction this component exists for became impossible to hit. Filling the
 * track keeps every category a real target, and the text line preserves the
 * "how full am I" reading.
 */
export function StorageBreakdownBar({
  usedBytes,
  limitBytes,
  usedPercent,
  breakdown,
  className = "",
}: StorageBreakdownBarProps) {
  const reduce = useReducedMotion();
  const [active, setActive] = useState<string | null>(null);

  const segments = useMemo(
    () => aggregateStorageBreakdown(breakdown, usedBytes),
    [breakdown, usedBytes],
  );

  // Trust `used_percent`, but derive it from the byte ratio if the server sent
  // something unusable, so the caption still reflects reality.
  const quotaPercent = useMemo(() => {
    const ratio = usedBytes > 0 && limitBytes > 0 ? (usedBytes / limitBytes) * 100 : 0;
    const pct = Number.isFinite(usedPercent) && usedPercent > 0 ? usedPercent : ratio;
    return Math.max(0, Math.min(100, pct));
  }, [usedPercent, usedBytes, limitBytes]);

  // No categorised data → nothing to show; the dashboard renders the plain bar.
  if (segments.length === 0) return null;

  const quotaLabel = formatStoragePercent(quotaPercent);
  const trackLabel = `Storage composition: ${formatBytes(usedBytes)} of ${formatBytes(
    limitBytes,
  )} used${quotaLabel ? ` (${quotaLabel})` : ""}`;

  return (
    <div className={className}>
      <div
        className="relative h-1.5 w-full rounded-full bg-outline"
        role="group"
        aria-label={trackLabel}
      >
        <motion.div
          className="flex h-full w-full"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        >
          {segments.map((segment, i) => (
            <Segment
              key={segment.category}
              segment={segment}
              first={i === 0}
              last={i === segments.length - 1}
              open={active === segment.category}
              reduce={reduce === true}
              onOpen={() => setActive(segment.category)}
              onClose={() => setActive((cur) => (cur === segment.category ? null : cur))}
            />
          ))}
        </motion.div>
      </div>

      {/* Quota context: the bar no longer encodes how much of the quota is used,
          so state it plainly. */}
      <p className="mt-1.5 text-[11px] leading-4 text-muted">
        <span className="font-medium text-on-background">{formatBytes(usedBytes)}</span> of{" "}
        {formatBytes(limitBytes)} used
        {quotaLabel && <span className="tabular-nums"> · {quotaLabel} of quota</span>}
      </p>

      {/* Always-visible legend: segments can be only a few pixels wide when one
          category dominates, so the numbers must also live outside the bar. */}
      <ul className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] leading-4 text-muted">
        {segments.map((segment) => {
          const percentLabel = formatStoragePercent(segment.percent);
          return (
            <li key={segment.category} className="inline-flex items-center gap-1.5">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: segment.color }}
                aria-hidden
              />
              <span className="font-medium text-on-background">{segment.label}</span>
              {percentLabel && (
                <span className="tabular-nums text-on-background">{percentLabel}</span>
              )}
              <span className="font-mono tabular-nums">· {formatBytes(segment.bytes)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Segment({
  segment,
  first,
  last,
  open,
  reduce,
  onOpen,
  onClose,
}: {
  segment: StorageBreakdownSegment;
  first: boolean;
  last: boolean;
  open: boolean;
  reduce: boolean;
  onOpen: () => void;
  onClose: () => void;
}) {
  const percentLabel = formatStoragePercent(segment.percent);
  const fileLabel =
    segment.fileCount > 0
      ? `, ${segment.fileCount} ${segment.fileCount === 1 ? "file" : "files"}`
      : "";

  // The popover is centred on its segment. Because segments near either end of
  // the track are close to the container edge, a centred popover can be clipped
  // by the viewport (and, on the right, add horizontal page overflow) — worst on
  // narrow viewports and with many small categories. Nudge it back inside once
  // it has been laid out; `useEffect` (not `useLayoutEffect`) keeps server
  // rendering warning-free.
  const tipRef = useRef<HTMLSpanElement>(null);
  const [nudge, setNudge] = useState(0);

  useEffect(() => {
    if (!open) {
      setNudge(0);
      return;
    }
    const el = tipRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const margin = 8;
    const vw = document.documentElement.clientWidth;
    let dx = 0;
    if (rect.left < margin) dx = margin - rect.left;
    else if (rect.right > vw - margin) dx = vw - margin - rect.right;
    setNudge(dx);
  }, [open]);

  return (
    <div
      role="img"
      tabIndex={0}
      aria-label={`${segment.label}: ${
        percentLabel ? `${percentLabel} of storage used, ` : ""
      }${formatBytes(segment.bytes)}${fileLabel}`}
      onMouseEnter={onOpen}
      onMouseLeave={onClose}
      onFocus={onOpen}
      onBlur={onClose}
      className={`relative h-full min-w-0 transition-[filter] hover:brightness-110 focus-visible:brightness-110 ${
        first ? "rounded-l-full" : ""
      } ${last ? "rounded-r-full" : ""}`}
      // Widths come from flex-grow so even a sub-pixel category keeps a
      // (rounding-error sized) sliver instead of collapsing entirely.
      style={{
        flexGrow: segment.bytes,
        flexBasis: 0,
        backgroundColor: segment.color,
        // Hairline separator drawn inside the segment: unlike a real gap it
        // costs no layout width, which matters at these segment sizes.
        boxShadow: last ? undefined : "inset -1px 0 0 0 var(--color-background)",
      }}
    >
      <AnimatePresence>
        {open && (
          // Positioned outside the bar; no ancestor clips overflow, so the
          // popover stays visible. `pointer-events-none` keeps hover stable.
          // `nudge` shifts it back inside the viewport at the track's edges.
          <span
            ref={tipRef}
            className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 block"
            style={{ transform: `translateX(calc(-50% + ${nudge}px))` }}
          >
            <motion.span
              className="block whitespace-nowrap rounded-lg border border-outline bg-surface px-2.5 py-1 text-xs shadow-xl"
              initial={reduce ? false : { opacity: 0, y: 4, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={reduce ? undefined : { opacity: 0, y: 4, scale: 0.97 }}
              transition={{ duration: 0.14, ease: [0.22, 1, 0.36, 1] }}
            >
              <span className="font-semibold text-on-background">{segment.label}</span>
              {percentLabel && (
                <>
                  <span className="mx-1 text-muted">·</span>
                  <span className="tabular-nums text-on-background">{percentLabel}</span>
                </>
              )}
              <span className="mx-1 text-muted">·</span>
              <span className="font-mono tabular-nums text-muted">{formatBytes(segment.bytes)}</span>
              {segment.fileCount > 0 && (
                <>
                  <span className="mx-1 text-muted">·</span>
                  <span className="tabular-nums text-muted">
                    {segment.fileCount} {segment.fileCount === 1 ? "file" : "files"}
                  </span>
                </>
              )}
            </motion.span>
          </span>
        )}
      </AnimatePresence>
    </div>
  );
}
