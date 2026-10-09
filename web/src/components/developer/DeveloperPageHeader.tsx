import type { ReactNode } from "react";

/**
 * The page header for a Developer page.
 *
 * Kept separate from the pages so the two share one visual weight and the
 * sidebar's "Developer" grouping is echoed by a breadcrumb here. The title is a
 * real `<h1>` so the page has a correct heading outline for screen readers.
 */
export function DeveloperPageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-5">
      <p className="mb-1 font-mono text-xs uppercase tracking-wider text-muted">Developer</p>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-on-background">{title}</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
