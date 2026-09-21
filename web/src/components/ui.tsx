import type { InputHTMLAttributes, ReactNode } from "react";
import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import { Coins } from "@phosphor-icons/react";
import { formatMeta, statusMeta } from "@/lib/format";
import { formatTint } from "@/lib/formatVisual";
import { FormatThumb, type FormatThumbSize } from "@/components/FormatThumb";

/* ---------------- Skeleton (loading) ---------------- */

/** A single shimmering gray block. */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded-md bg-surface-variant ${className}`}
      aria-hidden
    >
      <motion.div
        className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent"
        animate={{ x: ["-100%", "100%"] }}
        transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}

/** Two lines of skeleton text (a label + a value). */
export function SkeletonText({ lines = 2 }: { lines?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className={i === lines - 1 ? "h-3 w-2/3" : "h-3 w-full"} />
      ))}
    </div>
  );
}

/** A skeleton for a stat/analytics card. */
export function StatCardSkeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-xl border border-outline bg-surface p-5 ${className}`}>
      <div className="mb-2 flex items-center gap-2">
        <Skeleton className="h-4 w-4" />
        <Skeleton className="h-3 w-24" />
      </div>
      <Skeleton className="h-8 w-16" />
      <Skeleton className="mt-3 h-1.5 w-full" />
    </div>
  );
}

/** Centered page loader with an animated morph + label. */
export function PageLoader({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex min-h-[40vh] flex-col items-center justify-center gap-4">
      <motion.span
        className="inline-flex items-center gap-1.5"
        aria-hidden
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
      >
        <FormatThumb format="pdf" size="sm" />
        <span className="block h-0.5 w-5 rounded-full bg-gradient-to-r from-fmt-pdf to-fmt-word" />
        <FormatThumb format="docx" size="sm" />
      </motion.span>
      <p className="text-sm text-muted">{label}</p>
    </div>
  );
}

/* ---------------- Button ---------------- */

type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive";

interface ButtonProps extends HTMLMotionProps<"button"> {
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
}

const BTN: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-on-primary hover:bg-primary/90 disabled:opacity-50",
  secondary:
    "border border-outline-strong text-on-background hover:bg-surface-variant disabled:opacity-50",
  ghost: "text-on-background hover:bg-surface-variant disabled:opacity-50",
  destructive: "text-error hover:bg-error/10 disabled:opacity-50",
};

const BTN_SIZE = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-base",
};

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ButtonProps) {
  const reduce = useReducedMotion();
  return (
    <motion.button
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition-colors disabled:cursor-not-allowed ${BTN[variant]} ${BTN_SIZE[size]} ${className}`}
      whileHover={reduce ? undefined : { scale: 1.03, y: -1 }}
      whileTap={reduce ? undefined : { scale: 0.97 }}
      transition={{ type: "spring", stiffness: 400, damping: 22 }}
      {...props}
    />
  );
}

/* ---------------- Card ---------------- */

export function Card({
  className = "",
  children,
  hover = false,
}: {
  className?: string;
  children: ReactNode;
  hover?: boolean;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={`rounded-xl border border-outline bg-surface ${hover ? "transition-colors hover:border-primary/40" : ""} ${className}`}
      whileHover={hover && !reduce ? { y: -3, scale: 1.005 } : undefined}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
    >
      {children}
    </motion.div>
  );
}

/* ---------------- Badge ---------------- */

export function Badge({
  color = "var(--color-muted)",
  pulse = false,
  children,
}: {
  color?: string;
  pulse?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold"
      // `formatTint` (color-mix) rather than `${color}1a`: appending an alpha
      // hex suffix to a `var(--token)` colour is invalid CSS and is dropped, so
      // the pill lost its tinted fill entirely.
      style={{ color, backgroundColor: formatTint(color, 10) }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{
          backgroundColor: color,
          animation: pulse ? "pulse-dot 1.6s ease-in-out infinite" : undefined,
        }}
      />
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const meta = statusMeta(status);
  return (
    <Badge color={meta.color} pulse={meta.pulse}>
      {meta.label}
    </Badge>
  );
}

/** A small pill showing how many credits/tokens a completed conversion used. */
export function CreditsBadge({ credits }: { credits: number }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-outline bg-surface-variant/60 px-2 py-0.5 font-mono text-xs font-semibold text-muted"
      title="Tokens used"
      aria-label={`${credits} tokens used`}
    >
      <Coins size={13} weight="fill" className="text-primary/80" aria-hidden />
      <span>{credits}</span>
    </span>
  );
}

/* ---------------- FormatChip + FormatMorph ---------------- */

/** Format thumbnail + the format name. Used in queue/history tables and cards. */
export function FormatChip({
  format,
  size = "sm",
}: {
  format: string;
  size?: FormatThumbSize;
}) {
  const meta = formatMeta(format);
  return (
    <span className="inline-flex items-center gap-1.5">
      {/* label="" — the format name is rendered below, so the tile is decorative */}
      <FormatThumb format={format} size={size} label="" />
      <span
        className="font-mono text-xs font-semibold whitespace-nowrap"
        style={{ color: meta.color }}
      >
        {meta.label}
      </span>
    </span>
  );
}

/** The brand signature: source → target thumbnails joined by a gradient stream. */
export function FormatMorph({
  from,
  to,
  size = "md",
  animated = true,
}: {
  from: string;
  to: string;
  size?: "sm" | "md" | "lg";
  animated?: boolean;
}) {
  const f = formatMeta(from);
  const t = formatMeta(to);
  const thumbSize: FormatThumbSize = size === "lg" ? "lg" : size === "sm" ? "sm" : "md";
  // The `sm` tile is too small to hold its own extension badge, so name the
  // format in text beside it — otherwise a dense row would show two anonymous
  // icons and the target format would be unreadable.
  const showText = size === "sm";
  return (
    <motion.span
      className="inline-flex items-center gap-1.5"
      role="img"
      aria-label={`${f.label} to ${t.label}`}
      layout
      transition={{ type: "spring", stiffness: 320, damping: 26 }}
    >
      <motion.span
        key={f.label}
        className="inline-flex items-center gap-1"
        initial={animated ? { scale: 0.6, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 380, damping: 20 }}
      >
        <FormatThumb format={from} size={thumbSize} label={showText ? "" : undefined} />
        {showText && (
          <span
            className="font-mono text-xs font-semibold whitespace-nowrap"
            style={{ color: f.color }}
          >
            {f.label}
          </span>
        )}
      </motion.span>
      <span className="inline-flex items-center" aria-hidden>
        <span
          className="block h-0.5 w-4 rounded-full"
          style={{
            background: `linear-gradient(90deg, ${f.color}, ${t.color})`,
            animation: animated ? "morph-flow 1.6s linear infinite" : undefined,
          }}
        />
        <motion.svg
          width="10"
          height="10"
          viewBox="0 0 10 10"
          className="shrink-0"
          animate={animated ? { x: [0, 2, 0] } : undefined}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
        >
          <path d="M1 5h7M5 2l3 3-3 3" stroke={t.color} strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </motion.svg>
      </span>
      <motion.span
        key={t.label}
        className="inline-flex items-center gap-1"
        initial={animated ? { scale: 0.6, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 380, damping: 20, delay: 0.05 }}
      >
        <FormatThumb format={to} size={thumbSize} label={showText ? "" : undefined} />
        {showText && (
          <span
            className="font-mono text-xs font-semibold whitespace-nowrap"
            style={{ color: t.color }}
          >
            {t.label}
          </span>
        )}
      </motion.span>
    </motion.span>
  );
}

/* ---------------- ProgressBar ---------------- */

export function ProgressBar({
  value,
  from = "var(--color-primary)",
  to,
  className = "",
}: {
  /**
   * Completed percentage, or `null` when the server has not reported one. A
   * `null` renders an *indeterminate* bar: the worker does not always send a
   * percentage, and inventing one means `aria-valuenow` announces a number that
   * never came from the backend (the pages used to pass a made-up `45`).
   */
  value: number | null;
  from?: string;
  to?: string;
  className?: string;
}) {
  const end = to ?? from;
  const background = `linear-gradient(90deg, ${from}, ${end})`;

  if (value === null) {
    return (
      <div
        className={`h-1.5 w-full overflow-hidden rounded-full bg-outline ${className}`}
        role="progressbar"
      >
        {/* An animated sweep, so a running job still reads as "moving". */}
        <motion.div
          className="h-full w-1/3 rounded-full"
          animate={{ x: ["-100%", "300%"] }}
          transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
          style={{ background }}
        />
      </div>
    );
  }

  const clamp = Math.max(0, Math.min(100, value));
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-outline ${className}`} role="progressbar" aria-valuenow={clamp} aria-valuemin={0} aria-valuemax={100}>
      <motion.div
        className="h-full rounded-full"
        initial={{ width: 0 }}
        animate={{ width: `${clamp}%` }}
        transition={{ type: "spring", stiffness: 120, damping: 22 }}
        style={{ background }}
      />
    </div>
  );
}

/* ---------------- Field ---------------- */

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
}

export function Field({ label, hint, className = "", id, ...props }: FieldProps) {
  const fieldId = id ?? label.toLowerCase().replace(/\s+/g, "-");
  return (
    <div className="space-y-1.5">
      <label htmlFor={fieldId} className="block text-sm font-medium text-on-background">
        {label}
      </label>
      <input
        id={fieldId}
        className={`h-11 w-full rounded-lg border border-outline-strong bg-surface-variant px-3 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none ${className}`}
        {...props}
      />
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* ---------------- Logo ---------------- */

export function Logo({ withWordmark = true }: { withWordmark?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="flex items-center gap-0.5" aria-hidden>
        <FormatThumb format="pdf" size="xs" />
        <span className="block h-0.5 w-3 rounded-full bg-gradient-to-r from-fmt-pdf to-fmt-word" />
        <FormatThumb format="docx" size="xs" />
      </span>
      {withWordmark && (
        <span className="font-display text-lg font-semibold tracking-tight text-on-background">
          Transform
        </span>
      )}
    </span>
  );
}
