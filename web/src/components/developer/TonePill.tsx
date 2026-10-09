import type { ReactNode } from "react";

/**
 * A semantic status pill.
 *
 * Colour is never the only signal: every pill carries a text label, and the
 * shape/fill differ per tone so the state is distinguishable in monochrome and
 * to a screen reader. This is the shared vocabulary for both Developer pages —
 * HTTP outcomes, connection states and tool outcomes all render through it, so
 * "green means success" is stated in exactly one place.
 */

export type Tone = "success" | "warning" | "error" | "neutral" | "info";

const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-success-container text-on-success-container border-success/30",
  warning: "bg-warning-container text-on-warning-container border-warning/30",
  error: "bg-error-container text-on-error-container border-error/30",
  neutral: "bg-surface-variant text-muted border-outline",
  info: "bg-primary-container text-on-primary-container border-primary/30",
};

const TONE_DOT: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  error: "bg-error",
  neutral: "bg-muted",
  info: "bg-primary",
};

export function TonePill({
  tone,
  children,
  title,
  className = "",
}: {
  tone: Tone;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-xs font-medium tabular-nums ${TONE_CLASSES[tone]} ${className}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`} />
      {children}
    </span>
  );
}
