import type { ReactNode } from "react";
import { Reveal } from "@/lib/motion";

/** A page section with the shared max-width and vertical rhythm. */
export function Section({
  id,
  className = "",
  children,
}: {
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className={`mx-auto max-w-6xl scroll-mt-20 px-4 py-14 sm:px-6 ${className}`}>
      {children}
    </section>
  );
}

/** A small uppercase label that opens a section. */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">{children}</p>
  );
}

/**
 * The heading block a section opens with: an optional eyebrow, a display-font
 * title, and an optional intro paragraph. Centralised so every marketing
 * section on the public site shares one typographic rhythm.
 */
export function SectionHeading({
  eyebrow,
  title,
  intro,
  className = "",
}: {
  eyebrow?: string;
  title: ReactNode;
  intro?: ReactNode;
  className?: string;
}) {
  return (
    <Reveal className={`max-w-2xl ${className}`}>
      {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
      <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h2>
      {intro && <p className="mt-3 text-muted">{intro}</p>}
    </Reveal>
  );
}
