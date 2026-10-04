import { Check, FileLock, ShieldCheck } from "@phosphor-icons/react";
import { Reveal } from "@/lib/motion";

/**
 * Only statements that are true of this product. No invented statistics,
 * customer counts, logos or testimonials.
 */
const TRUST_POINTS = [
  { icon: Check, label: "Cancel anytime" },
  { icon: ShieldCheck, label: "Payments secured by Stripe" },
  { icon: FileLock, label: "Files encrypted at rest" },
];

export function PricingHero() {
  return (
    <Reveal className="mb-12 text-center">
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">Pricing</p>
      <h1 className="font-display text-4xl font-semibold tracking-tight">
        Simple plans. Real power.
      </h1>
      <p className="mx-auto mt-3 max-w-xl text-muted">
        Start free and upgrade when the work grows. Monthly billing, no lock-in.
      </p>

      <ul className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm text-muted">
        {TRUST_POINTS.map(({ icon: Icon, label }) => (
          <li key={label} className="inline-flex items-center gap-1.5">
            <Icon size={15} weight="bold" className="shrink-0 text-success" aria-hidden />
            {label}
          </li>
        ))}
      </ul>

      <div className="mt-6">
        <a
          href="#compare"
          className="text-sm font-semibold text-primary hover:underline"
        >
          Compare all features
        </a>
      </div>
    </Reveal>
  );
}
