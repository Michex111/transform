import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowCounterClockwise, LockSimple, Trash } from "@phosphor-icons/react";
import { CreditsBadge, FormatMorph, ProgressBar } from "@/components/ui";
import { formatBytes, formatMeta } from "@/lib/format";
import { jobDetails } from "@/lib/jobDetails";
import type { UiJob } from "@/jobs/JobsContext";

interface JobDetailsPanelProps {
  /** Whether the owning row is expanded. */
  open: boolean;
  job: UiJob;
  /** `id` the row's disclosure button points at with `aria-controls`. */
  id: string;
  /**
   * True in the card layout below `md`, where the row has no inline actions, so
   * the panel has to supply Delete/Retry itself.
   *
   * From `md` up the row keeps its own action buttons — they are the fast path a
   * table exists for — and the panel is purely informational: two delete buttons
   * for one record, one of them buried in a disclosure, is worse than one.
   */
  compact?: boolean;
  onDelete?: (job: UiJob) => void;
  onRetry?: (job: UiJob) => void;
}

/**
 * The disclosure panel a job row expands into, at every breakpoint.
 *
 * WHY IT EXISTS
 * Below `md` a row cannot carry a table's worth of columns: reserving tracks for
 * format, status, date and a variable number of action buttons squeezed the
 * filename to nothing (36px on History, 0px on the Dashboard). The row now shows
 * the few things that answer "what happened to my file?" and everything else
 * lives here, one click away. On desktop the columns are legible, but the
 * interesting facts — byte sizes, the exact duration, the failure reason — have
 * no column of their own, so the same disclosure serves both widths.
 *
 * WHY IT IS MOUNTED CONDITIONALLY
 * `AnimatePresence` keeps the panel out of the tree entirely while collapsed.
 * That is not only cheaper: the desktop row already renders a token badge in its
 * actions column, so an always-mounted panel would state the same fact twice in
 * the document.
 *
 * The height transition animates `height: auto`, so the panel measures its own
 * content and no consumer has to guess a max-height that a long filename or a
 * wrapped error message would silently clip.
 */
export function JobDetailsPanel({
  open,
  job,
  id,
  compact = false,
  onDelete,
  onRetry,
}: JobDetailsPanelProps) {
  const reduce = useReducedMotion();
  const fileName = job.fileName ?? job.input_file;
  const details = jobDetails(job);
  const source = formatMeta(job.source_format);
  const target = formatMeta(job.target_format);
  const canRetry = job.status === "FAILED" && onRetry !== undefined;
  // Only the card layout needs the actions; see `compact` above.
  const showActions = compact && (onDelete !== undefined || canRetry);

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          key="details"
          id={id}
          role="region"
          aria-label={`Details for ${fileName}`}
          // The panel must occupy its own line and bleed to the row's edges,
          // which takes a different mechanism per layout because the row is a
          // flex line below `md` and a grid from `md` up:
          //   • flex  — `basis-[calc(100%+2rem)]` wraps it onto its own line at
          //     the row's full width, and `-mx-4` cancels the row's `px-4` so
          //     the surface reaches the edges while the content stays aligned
          //     with the filename above it.
          //   • grid  — `col-span-full` is what makes it span every column (a
          //     `basis-*` is inert in a grid, which silently confined the panel
          //     to the 2fr filename column), and `md:-mx-5` cancels `md:px-5`.
          // Verified at 360px and 1280px: the panel's box is identical to the
          // row's and its first line starts at the same x as the filename.
          // `overflow-hidden` is what lets the height animate.
          className="-mx-4 basis-[calc(100%+2rem)] overflow-hidden md:col-span-full md:-mx-5"
          initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { height: "auto", opacity: 1 }}
          exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
          transition={
            reduce
              ? { duration: 0.01 }
              : {
                  height: { duration: 0.26, ease: [0.22, 1, 0.36, 1] },
                  opacity: { duration: 0.18, ease: "easeOut" },
                }
          }
        >
          <motion.div
            // A short fade-down of the contents so the panel does not read as a
            // box that merely got taller.
            initial={reduce ? false : { opacity: 0, y: -6 }}
            animate={reduce ? undefined : { opacity: 1, y: 0 }}
            transition={{ duration: 0.24, delay: 0.05, ease: [0.22, 1, 0.36, 1] }}
            // Padding tracks the row's (`px-4`, `md:px-5`) so the content lines
            // up with the filename at every width; see the `-mx` above.
            className="border-t border-outline bg-surface-variant/40 px-4 pb-4 pt-3 md:px-5"
          >
            {/* The brand's morph stream, reused as the panel's accent so the
                expanded row is still recognisably this product. */}
            <span
              aria-hidden
              className="mb-2.5 block h-0.5 w-10 rounded-full"
              style={{ background: `linear-gradient(90deg, ${source.color}, ${target.color})` }}
            />
            {/* The row truncates this, so the panel is where it is readable. */}
            <p className="break-words font-mono text-xs font-medium leading-relaxed text-on-background">
              {fileName}
            </p>

            <dl className="mt-3 grid grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)] items-center gap-x-3 gap-y-2.5 sm:grid-cols-[minmax(0,7rem)_minmax(0,1fr)]">
              {details.map((detail) => (
                <DetailRow key={detail.key} detail={detail} />
              ))}
            </dl>

            {showActions && (
              <div className="mt-4 flex items-center gap-2 border-t border-outline pt-3">
                {canRetry && (
                  <button
                    type="button"
                    onClick={(e) => {
                      // The row itself toggles on click; an action must not.
                      e.stopPropagation();
                      onRetry(job);
                    }}
                    className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-primary/40 bg-primary-container/40 px-3 text-xs font-semibold text-on-primary-container transition-colors hover:bg-primary-container active:bg-primary-container"
                  >
                    <ArrowCounterClockwise size={15} /> Retry
                  </button>
                )}
                {onDelete && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(job);
                    }}
                    className="inline-flex min-h-11 items-center gap-1.5 rounded-lg border border-outline px-3 text-xs font-semibold text-muted transition-colors hover:border-error/50 hover:text-error active:border-error/50 active:text-error"
                  >
                    <Trash size={15} /> Delete
                  </button>
                )}
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** One line of the panel's definition list. */
function DetailRow({ detail }: { detail: ReturnType<typeof jobDetails>[number] }) {
  const label = (
    <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted">
      {detail.label}
    </dt>
  );

  switch (detail.kind) {
    case "conversion":
      return (
        <>
          {label}
          <dd className="min-w-0">
            <FormatMorph from={detail.from} to={detail.to} size="sm" animated={false} />
          </dd>
        </>
      );
    case "progress":
      return (
        <>
          {label}
          <dd className="min-w-0">
            <ProgressBar value={detail.percent} from="var(--color-primary)" />
          </dd>
        </>
      );
    case "size":
      return (
        <>
          {label}
          {/* `tabular-nums` so the two size lines line up digit-for-digit. */}
          <dd className="min-w-0 font-mono text-xs tabular-nums text-on-background">
            {formatBytes(detail.bytes)}
          </dd>
        </>
      );
    case "tokens":
      return (
        <>
          {label}
          <dd className="min-w-0">
            <CreditsBadge credits={detail.credits} />
          </dd>
        </>
      );
    case "encrypted":
      return (
        <>
          {label}
          <dd className="flex min-w-0 items-center gap-1.5 text-xs text-success">
            <LockSimple size={13} weight="fill" aria-hidden />
            {detail.value}
          </dd>
        </>
      );
    case "error":
      return (
        <>
          {label}
          <dd className="min-w-0 break-words text-xs leading-relaxed text-error">
            {detail.message}
          </dd>
        </>
      );
    default:
      // Every remaining kind is a plain string value.
      return (
        <>
          {label}
          <dd className="min-w-0 break-words font-mono text-xs text-on-background">
            {detail.value}
          </dd>
        </>
      );
  }
}
