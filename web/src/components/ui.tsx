import type { InputHTMLAttributes, ReactNode } from "react";
import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import { formatMeta, statusMeta } from "@/lib/format";

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
export function StatCardSkeleton() {
  return (
    <div className="rounded-xl border border-outline bg-surface p-5">
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
        <span className="inline-flex h-7 items-center rounded-md bg-fmt-pdf/20 px-2 font-mono text-xs font-bold text-fmt-pdf">PDF</span>
        <span className="block h-0.5 w-5 rounded-full bg-gradient-to-r from-fmt-pdf to-fmt-word" />
        <span className="inline-flex h-7 items-center rounded-md bg-fmt-word/20 px-2 font-mono text-xs font-bold text-fmt-word">DOC</span>
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
      style={{ color, backgroundColor: `${color}1a` }}
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

/* ---------------- FormatChip + FormatMorph ---------------- */

export function FormatChip({ format }: { format: string }) {
  const meta = formatMeta(format);
  return (
    <span
      className="inline-flex items-center rounded-md px-2 py-0.5 font-mono text-xs font-semibold"
      style={{ color: meta.color, backgroundColor: `${meta.color}1a`, border: `1px solid ${meta.color}40` }}
    >
      {meta.label}
    </span>
  );
}

/** The brand signature: source → target chips joined by a gradient stream. */
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
  const chip = size === "lg" ? "px-3 py-1.5 text-sm" : size === "sm" ? "px-1.5 py-0.5 text-xs" : "px-2 py-1 text-xs";
  return (
    <motion.span
      className="inline-flex items-center gap-1.5"
      aria-label={`${f.label} to ${t.label}`}
      layout
      transition={{ type: "spring", stiffness: 320, damping: 26 }}
    >
      <motion.span
        key={f.label}
        className={`inline-flex items-center rounded-md font-mono font-semibold ${chip}`}
        style={{ color: f.color, backgroundColor: `${f.color}1a`, border: `1px solid ${f.color}40` }}
        initial={animated ? { scale: 0.6, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 380, damping: 20 }}
      >
        {f.label}
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
        className={`inline-flex items-center rounded-md font-mono font-semibold ${chip}`}
        style={{ color: t.color, backgroundColor: `${t.color}1a`, border: `1px solid ${t.color}40` }}
        initial={animated ? { scale: 0.6, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 380, damping: 20, delay: 0.05 }}
      >
        {t.label}
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
  value: number;
  from?: string;
  to?: string;
  className?: string;
}) {
  const clamp = Math.max(0, Math.min(100, value));
  const end = to ?? from;
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-outline ${className}`} role="progressbar" aria-valuenow={clamp} aria-valuemin={0} aria-valuemax={100}>
      <motion.div
        className="h-full rounded-full"
        initial={{ width: 0 }}
        animate={{ width: `${clamp}%` }}
        transition={{ type: "spring", stiffness: 120, damping: 22 }}
        style={{
          background: `linear-gradient(90deg, ${from}, ${end})`,
        }}
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
        <span className="inline-flex h-6 items-center rounded-md bg-fmt-pdf/20 px-1.5 font-mono text-[10px] font-bold text-fmt-pdf">PDF</span>
        <span className="block h-0.5 w-3 rounded-full bg-gradient-to-r from-fmt-pdf to-fmt-word" />
        <span className="inline-flex h-6 items-center rounded-md bg-fmt-word/20 px-1.5 font-mono text-[10px] font-bold text-fmt-word">DOC</span>
      </span>
      {withWordmark && (
        <span className="font-display text-lg font-semibold tracking-tight text-on-background">
          Transform
        </span>
      )}
    </span>
  );
}
