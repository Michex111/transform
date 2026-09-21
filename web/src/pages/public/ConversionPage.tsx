// ConversionPage — the `/{from}-to-{to}` conversion detail page.
//
// The pair is checked against the live conversion graph before anything is
// rendered as a converter: an unsupported pair gets an honest explanation and
// the targets that DO exist, never a broken widget with a dead link.

import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, WarningCircle } from "@phosphor-icons/react";
import { Button, Skeleton } from "@/components/ui";
import { ConverterCard } from "@/components/ConverterCard";
import { FormatLink, FormatLinkGrid } from "@/components/FormatLink";
import { formatVisual } from "@/lib/formatVisual";
import { useConversionMap } from "@/lib/useConversionMap";
import { Reveal } from "@/lib/motion";

/** Cap on how many sibling pairs we list so the page stays scannable. */
const MAX_RELATED = 12;

interface ConversionPageProps {
  /** Normalised source extension, e.g. "pdf". */
  from: string;
  /** Normalised target extension, e.g. "docx". */
  to: string;
}

export function ConversionPage({ from, to }: ConversionPageProps) {
  const navigate = useNavigate();
  const { targetsFor, sourcesFor, supported, loading, error } = useConversionMap({ guest: true });

  const fromVisual = formatVisual(from);
  const toVisual = formatVisual(to);

  const targets = targetsFor(from);
  const sources = sourcesFor(to);
  const isSupported = targets.includes(to);
  // Three distinct failure modes, and the copy must not conflate them: the
  // source may be absent from the graph entirely, present only as a target, or
  // a real source that simply cannot reach this particular target.
  const fromKnown = supported.has(from);
  const fromIsSource = targets.length > 0;

  const relatedTargets = targets.filter((t) => t !== to);
  const relatedSources = sources.filter((s) => s !== from);

  /* ---------------- loading ---------------- */
  if (loading) {
    return (
      <div className="format-glyph-field">
        <div className="mx-auto max-w-4xl space-y-8 px-4 py-12 sm:px-6">
          <div className="space-y-3">
            <Skeleton className="h-6 w-44" />
            <Skeleton className="h-9 w-80" />
            <Skeleton className="h-4 w-full max-w-lg" />
          </div>
          <Skeleton className="h-[8.5rem] w-full max-w-md rounded-2xl" />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" aria-hidden>
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
          <p className="text-sm text-muted" role="status">
            Loading {fromVisual.label} to {toVisual.label}…
          </p>
        </div>
      </div>
    );
  }

  /* ---------------- graph unavailable ---------------- */
  if (error) {
    return (
      <div className="format-glyph-field">
        <div className="mx-auto max-w-2xl px-4 py-20 sm:px-6">
          <div className="rounded-xl border border-outline bg-surface p-8 text-center">
            <WarningCircle size={28} className="mx-auto mb-3 text-warning" aria-hidden />
            <h1 className="mb-2 font-display text-2xl font-semibold">
              {fromVisual.label} to {toVisual.label} Converter
            </h1>
            <p className="mx-auto mb-6 max-w-md text-sm text-muted">
              We could not load the list of supported conversions, so we cannot confirm this pair
              right now. You can still open the converter and try the file.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/convert">
                <Button>
                  Open the converter <ArrowRight size={16} />
                </Button>
              </Link>
              <Link to={`/${from}-converter`}>
                <Button variant="secondary">All {fromVisual.label} conversions</Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- pair not in the graph ---------------- */
  if (!isSupported) {
    return (
      <div className="format-glyph-field">
        <div className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            {fromVisual.label} to {toVisual.label} is not supported
          </h1>
          <p className="mt-3 max-w-2xl text-muted">
            {!fromKnown
              ? `${fromVisual.label} is not part of our supported conversion graph, so there is nothing to convert. Pick a format from the catalogue instead.`
              : fromIsSource
                ? `Our converter accepts ${fromVisual.label} as an input, but it cannot produce ${toVisual.label}. Nothing is broken — this pair simply is not part of our conversion graph.`
                : `Our converter does not accept ${fromVisual.label} as an input — it can only be produced as an output.`}
          </p>

          <section className="mt-10">
            <h2 className="mb-4 font-display text-xl font-semibold">
              {fromIsSource ? `What ${fromVisual.label} can become` : `Supported conversions`}
            </h2>
            {targets.length > 0 ? (
              <FormatLinkGrid>
                {targets.map((target) => (
                  <FormatLink key={target} to={`/${from}-to-${target}`} format={target}>
                    {`${fromVisual.label} to ${formatVisual(target).label}`}
                  </FormatLink>
                ))}
              </FormatLinkGrid>
            ) : (
              <p className="rounded-xl border border-outline bg-surface p-5 text-sm text-muted">
                {fromKnown
                  ? `${fromVisual.label} is currently only supported as a conversion target.`
                  : `${fromVisual.label} is not supported in either direction. Browse the format catalogue to see everything we can convert.`}
              </p>
            )}
          </section>

          <div className="mt-10 flex flex-wrap items-center gap-3">
            <Link to={`/${from}-converter`}>
              <Button>
                All {fromVisual.label} conversions <ArrowRight size={16} />
              </Button>
            </Link>
            <Link to="/convert">
              <Button variant="secondary">Open the converter</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- happy path ---------------- */
  return (
    <div className="format-glyph-field">
      <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
        <Reveal>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
            Conversion
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            {fromVisual.label} to {toVisual.label} Converter
          </h1>
          <p className="mt-3 max-w-2xl text-muted">
            Move a file from {fromVisual.label} to {toVisual.label}. {fromVisual.label} has{" "}
            {targets.length} available target{targets.length === 1 ? "" : "s"}, and{" "}
            {sources.length} format{sources.length === 1 ? "" : "s"} convert into{" "}
            {toVisual.label}. Free, no sign-up required.
          </p>
        </Reveal>

        <Reveal className="mt-8">
          <div className="flex flex-wrap items-center gap-8 rounded-2xl border border-outline bg-surface/60 p-6">
            <ConverterCard
              from={from}
              to={to}
              onFromClick={() => navigate(`/${from}-converter`)}
              onToClick={() => navigate(`/${to}-converter`)}
            />
            <div className="flex flex-col gap-3">
              <Link to="/convert" state={{ source: from, target: to }}>
                <Button size="lg">
                  Convert {fromVisual.label} to {toVisual.label} <ArrowRight size={18} />
                </Button>
              </Link>
              <p className="max-w-xs text-xs text-muted">
                Click either format above to see everything it converts to or from.
              </p>
            </div>
          </div>
        </Reveal>

        {relatedTargets.length > 0 && (
          <section className="mt-12">
            <h2 className="mb-4 font-display text-xl font-semibold">
              Other conversions from {fromVisual.label}
            </h2>
            <FormatLinkGrid>
              {(relatedTargets.length > MAX_RELATED
                ? relatedTargets.slice(0, MAX_RELATED)
                : relatedTargets
              ).map((target) => (
                <FormatLink key={target} to={`/${from}-to-${target}`} format={target}>
                  {`${fromVisual.label} to ${formatVisual(target).label}`}
                </FormatLink>
              ))}
            </FormatLinkGrid>
            {relatedTargets.length > MAX_RELATED && (
              <p className="mt-3 text-sm">
                <Link to={`/${from}-converter`} className="text-primary hover:underline">
                  See all {targets.length} {fromVisual.label} conversions
                </Link>
              </p>
            )}
          </section>
        )}

        {relatedSources.length > 0 && (
          <section className="mt-12">
            <h2 className="mb-4 font-display text-xl font-semibold">
              Other conversions to {toVisual.label}
            </h2>
            <FormatLinkGrid>
              {(relatedSources.length > MAX_RELATED
                ? relatedSources.slice(0, MAX_RELATED)
                : relatedSources
              ).map((source) => (
                <FormatLink key={source} to={`/${source}-to-${to}`} format={source}>
                  {`${formatVisual(source).label} to ${toVisual.label}`}
                </FormatLink>
              ))}
            </FormatLinkGrid>
            {relatedSources.length > MAX_RELATED && (
              <p className="mt-3 text-sm">
                <Link to={`/${to}-converter`} className="text-primary hover:underline">
                  See all formats that convert to {toVisual.label}
                </Link>
              </p>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
