// ConverterCard — the compact "source file → target file" signature element.
//
// Two format faces (a neutral/tinted input, a brand-tinted output that
// breathes) are joined by a connector: two hairline rules with a light streak
// sweeping along them and a rotating operation glyph in the middle.
//
// All colour work goes through `formatTint` — never string-concatenate an
// alpha suffix onto a `var(--token)`, that produces invalid CSS which the
// browser silently drops.
//
// Motion is gated on `useReducedMotion()`; the global reduced-motion clamp in
// `index.css` only shortens durations, it does not cancel the hover transforms.

import type { CSSProperties } from "react";
import { useReducedMotion } from "motion/react";
import { ArrowsClockwise, CaretDown } from "@phosphor-icons/react";
import { formatTint, formatVisual } from "@/lib/formatVisual";

export type ConverterCardSize = "sm" | "md" | "lg";

export interface ConverterCardProps {
  /** Source format extension, e.g. "pdf". */
  from: string;
  /** Target format extension, e.g. "docx". */
  to: string;
  /** Makes the input face a real button. Omit for a non-interactive card. */
  onFromClick?: () => void;
  /** Makes the output face a real button. Omit for a non-interactive card. */
  onToClick?: () => void;
  /** Overrides the uppercase label on the input face. */
  fromLabel?: string;
  /** Overrides the uppercase label on the output face. */
  toLabel?: string;
  size?: ConverterCardSize;
  /** Show the small "TO" caption under the connector. */
  showToLabel?: boolean;
  className?: string;
}

interface SizeSpec {
  /** Face box sizing (includes its own responsive steps). */
  face: string;
  /** Gap between the two faces and the connector. */
  gap: string;
  /** Icon sizing class for the format glyph (28/32px on `md`, as the reference). */
  icon: string;
  /** Extension label sizing. */
  label: string;
  /** Inner stacking gap inside a face. */
  innerGap: string;
  /** Connector rule width. */
  rule: string;
  /** Operation dial box. */
  dial: string;
  /** Operation glyph size in px. */
  dialIcon: number;
  /** Bottom-right affordance chevron size in px. */
  chevron: number;
}

const SIZES: Record<ConverterCardSize, SizeSpec> = {
  sm: {
    face: "h-[5.5rem] w-20 rounded-xl",
    gap: "gap-2",
    icon: "size-6",
    label: "text-[11px]",
    innerGap: "gap-1.5",
    rule: "w-4",
    dial: "size-8",
    dialIcon: 14,
    chevron: 10,
  },
  md: {
    face: "h-[6.75rem] w-24 sm:h-[7.5rem] sm:w-28 rounded-[0.85rem]",
    gap: "gap-3 sm:gap-4",
    icon: "size-7 sm:size-8",
    label: "text-xs sm:text-sm",
    innerGap: "gap-2",
    rule: "w-5 sm:w-7",
    dial: "size-9 sm:size-10",
    dialIcon: 16,
    chevron: 10,
  },
  lg: {
    face: "h-[8rem] w-32 rounded-2xl",
    gap: "gap-4",
    icon: "size-9",
    label: "text-base",
    innerGap: "gap-2.5",
    rule: "w-8",
    dial: "size-11",
    dialIcon: 18,
    chevron: 12,
  },
};

/* ------------------------------------------------------------------ */
/* Inner pieces                                                        */
/* ------------------------------------------------------------------ */

/** One of the two hairline connector rules, with its sweeping light streak. */
function SweepRule({
  width,
  color,
  flip = false,
  reduce,
}: {
  width: string;
  /** Format colour the rule fades toward. */
  color: string;
  /** Mirrors the gradient so the two rules point at the dial. */
  flip?: boolean;
  reduce: boolean;
}) {
  const track = flip
    ? `linear-gradient(90deg, ${formatTint(color, 70, "var(--color-outline-strong)")}, var(--color-outline-strong))`
    : `linear-gradient(90deg, var(--color-outline-strong), ${formatTint(color, 70, "var(--color-outline-strong)")})`;

  return (
    <span aria-hidden className={`relative h-px overflow-hidden ${width}`} style={{ background: track }}>
      {!reduce && (
        <span
          className="absolute inset-0 block h-px animate-arrow-sweep"
          style={{
            background: `linear-gradient(90deg, transparent, ${formatTint(color, 85, "var(--color-on-primary-container)")}, transparent)`,
          }}
        />
      )}
    </span>
  );
}

/** A single format face: a real button when actionable, otherwise a static image. */
function ConverterFace({
  ext,
  text,
  spec,
  reduce,
  variant,
  faceAriaLabel,
  onActivate,
}: {
  ext: string;
  text: string;
  spec: SizeSpec;
  reduce: boolean;
  variant: "input" | "output";
  /** Accessible name without the interaction hint, e.g. "Input format: DOCX". */
  faceAriaLabel: string;
  onActivate?: () => void;
}) {
  const { color, Icon } = formatVisual(ext);
  const interactive = typeof onActivate === "function";
  const isOutput = variant === "output";

  const faceClass = [
    "group/card relative flex shrink-0 items-center justify-center overflow-hidden border backdrop-blur-md transition duration-300 ease-out",
    spec.face,
    isOutput ? "border-primary/35" : "border-outline-strong",
    "shadow-[inset_0_1px_0_rgb(255_255_255/0.06),0_8px_24px_rgb(0_0_0/0.35)]",
    interactive && !reduce
      ? isOutput
        ? "cursor-pointer hover:-translate-y-0.5 hover:border-primary/70 hover:shadow-[inset_0_1px_0_rgb(255_255_255/0.08),0_12px_30px_rgb(0_0_0/0.45)]"
        : "cursor-pointer hover:-translate-y-0.5 hover:border-on-background/30 hover:shadow-[inset_0_1px_0_rgb(255_255_255/0.08),0_12px_30px_rgb(0_0_0/0.45)]"
      : "",
    isOutput && !reduce ? "animate-output-pulse" : "",
  ]
    .filter(Boolean)
    .join(" ");

  // The glow is driven by the *target format's* colour so the pulse matches the
  // palette instead of a fixed hue; the keyframes fall back to neutral values.
  const style = (isOutput
    ? {
        "--output-glow-soft": formatTint(color, 32),
        "--output-glow-strong": formatTint(color, 58),
      }
    : {}) as CSSProperties;

  const body = (
    <>
      <span
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background: `linear-gradient(145deg, ${formatTint(color, isOutput ? 22 : 13)}, transparent 72%)`,
        }}
      />
      <span className={`relative flex h-full w-full flex-col items-center justify-center px-2 ${spec.innerGap}`}>
        <Icon
          className={`${spec.icon} transition-transform duration-300 group-hover/card:scale-110`}
          weight="duotone"
          aria-hidden
          style={{ color }}
        />
        <span
          className={`max-w-full truncate font-bold uppercase tracking-wider text-on-background ${spec.label}`}
        >
          {text}
        </span>
      </span>
      {interactive && (
        <CaretDown
          size={spec.chevron}
          weight="bold"
          aria-hidden
          className="absolute bottom-1.5 right-2 text-muted transition-colors group-hover/card:text-on-background"
        />
      )}
    </>
  );

  if (interactive) {
    return (
      <button
        type="button"
        onClick={onActivate}
        aria-label={`${faceAriaLabel}. Click to change.`}
        className={faceClass}
        style={style}
      >
        {body}
      </button>
    );
  }

  return (
    <div role="img" aria-label={faceAriaLabel} className={faceClass} style={style}>
      {body}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* ConverterCard                                                       */
/* ------------------------------------------------------------------ */

/**
 * Renders `from → to` as two format faces joined by an animated connector.
 *
 * Exposes an accessible name ("DOCX to PDF") on the wrapper so assistive
 * technology reads the pair as one widget; each interactive face is an
 * independent, individually labelled button.
 */
export function ConverterCard({
  from,
  to,
  onFromClick,
  onToClick,
  fromLabel,
  toLabel,
  size = "md",
  showToLabel = true,
  className = "",
}: ConverterCardProps) {
  const reduce = useReducedMotion() === true;
  const spec = SIZES[size];
  const fromVisual = formatVisual(from);
  const toVisual = formatVisual(to);
  const fromText = fromLabel ?? fromVisual.label;
  const toText = toLabel ?? toVisual.label;

  return (
    <div
      role="group"
      aria-label={`${fromText} to ${toText}`}
      className={`flex items-center ${spec.gap} ${className}`}
    >
      <ConverterFace
        ext={from}
        text={fromText}
        spec={spec}
        reduce={reduce}
        variant="input"
        faceAriaLabel={`Input format: ${fromText}`}
        onActivate={onFromClick}
      />

      <div className="flex flex-col items-center gap-2">
        <div className="flex items-center">
          <SweepRule width={spec.rule} color={toVisual.color} reduce={reduce} />
          {/* Decorative, non-interactive: a span (not a button) so it never
              enters the tab order. */}
          <span
            aria-hidden
            tabIndex={-1}
            className={`relative flex items-center justify-center rounded-full border backdrop-blur-sm transition duration-300 ease-out ${spec.dial} ${
              reduce ? "" : "hover:scale-110"
            }`}
            style={{
              borderColor: formatTint(toVisual.color, 45),
              backgroundColor: formatTint(toVisual.color, 15),
            }}
          >
            <ArrowsClockwise
              size={spec.dialIcon}
              aria-hidden
              className={reduce ? "" : "animate-spin-pulse"}
              style={{ color: "var(--color-on-primary-container)" }}
            />
            {!reduce && (
              <span
                className="pointer-events-none absolute inset-0 animate-ping rounded-full"
                style={{ boxShadow: `0 0 0 1px ${formatTint(toVisual.color, 25)}` }}
              />
            )}
          </span>
          <SweepRule width={spec.rule} color={toVisual.color} flip reduce={reduce} />
        </div>
        {showToLabel && (
          <span className="text-[0.65rem] font-medium uppercase tracking-[0.25em] text-muted">
            to
          </span>
        )}
      </div>

      <ConverterFace
        ext={to}
        text={toText}
        spec={spec}
        reduce={reduce}
        variant="output"
        faceAriaLabel={`Output format: ${toText}`}
        onActivate={onToClick}
      />
    </div>
  );
}
