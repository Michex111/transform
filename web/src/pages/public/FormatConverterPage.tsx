// FormatConverterPage — the `/{ext}-converter` format hub.
//
// Every count and every link on this page is read from the live conversion
// graph, so the page can never advertise a pair the backend cannot perform.
// While the graph loads we render skeletons; if the format is not in the graph
// we say so plainly instead of showing an empty shell.

import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, WarningCircle } from "@phosphor-icons/react";
import { Button, Skeleton } from "@/components/ui";
import { ConverterCard } from "@/components/ConverterCard";
import { FormatThumb } from "@/components/FormatThumb";
import { FormatLink, FormatLinkGrid } from "@/components/FormatLink";
import { formatVisual } from "@/lib/formatVisual";
import { useConversionMap } from "@/lib/useConversionMap";
import { Reveal } from "@/lib/motion";

interface FormatConverterPageProps {
  /** Normalised (lowercase, dot-free) format extension, e.g. "pdf". */
  ext: string;
}

/**
 * Target formats we prefer to showcase in the hero card, most broadly useful
 * first. Anything not listed falls back to the source's first real target.
 */
const PREFERRED_TARGETS = ["pdf", "docx", "jpg", "png", "mp3", "wav", "gif", "zip"];

/** Pluralise a count for the generated, factual copy. */
function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${n} ${n === 1 ? singular : pluralForm}`;
}

/** "a document" / "an archive" — the category names are vowel-sensitive. */
function article(word: string): string {
  return /^[aeiou]/i.test(word) ? "an" : "a";
}

export function FormatConverterPage({ ext }: FormatConverterPageProps) {
  const navigate = useNavigate();
  const { targetsFor, sourcesFor, supported, loading, error } = useConversionMap({ guest: true });
  const { label, category, Icon, color } = formatVisual(ext);

  const targets = targetsFor(ext);
  const sources = sourcesFor(ext);
  // Pick a recognisable default target rather than the alphabetically-first one
  // (which would make a PDF hub advertise "PDF to AZW"). Falls back to the first
  // real target when none of the common ones apply.
  const primaryTarget = targets.find((target) => PREFERRED_TARGETS.includes(target)) ?? targets[0];
  const primarySource = sources[0];
  const isSupported = supported.has(ext);

  // Formats that can only be a conversion *target* have no first target, so
  // rendering `ext → ext` would be meaningless. Flip the card to show how the
  // format is produced instead — so every journey shown is a real graph edge.
  const cardFrom = primaryTarget ? ext : (primarySource ?? ext);
  const cardTo = primaryTarget ?? ext;
  const cardPair = { source: cardFrom, target: cardTo };

  // Generated, fact-only copy: every clause comes from the real graph, and a
  // target-only format gets a sentence that reads correctly instead of
  // "convert to 0 other formats".
  const description =
    targets.length > 0
      ? `Convert ${label} to ${plural(targets.length, "other format")}${
          sources.length > 0
            ? `, or convert ${plural(sources.length, "format")} into ${label}`
            : ""
        }. Free, no sign-up required.`
      : `Convert ${plural(sources.length, "format")} into ${label}. Free, no sign-up required.`;

  const openConverter = () => navigate("/convert", { state: cardPair });

  /* ---------------- loading ---------------- */
  if (loading) {
    return (
      <div className="format-glyph-field">
        <div className="mx-auto max-w-4xl space-y-8 px-4 py-12 sm:px-6">
          <div className="space-y-3">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-9 w-72" />
            <Skeleton className="h-4 w-full max-w-lg" />
          </div>
          <Skeleton className="h-[8.5rem] w-full max-w-md rounded-2xl" />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" aria-hidden>
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
          <p className="text-sm text-muted" role="status">
            Loading {label} conversions…
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
            <h1 className="mb-2 font-display text-2xl font-semibold">{label} Converter</h1>
            <p className="mx-auto mb-6 max-w-md text-sm text-muted">
              We could not load the list of supported conversions, so we cannot show what {label}{" "}
              converts to right now. You can still open the converter and drop in a file.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/convert">
                <Button>
                  Open the converter <ArrowRight size={16} />
                </Button>
              </Link>
              <Link to="/">
                <Button variant="secondary">Back home</Button>
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ---------------- format not in the graph ---------------- */
  if (!isSupported) {
    return (
      <div className="format-glyph-field">
        <div className="mx-auto max-w-2xl px-4 py-20 sm:px-6">
          <div className="rounded-xl border border-outline bg-surface p-8 text-center">
            <FormatThumb format={ext} size="lg" label="" className="mx-auto mb-4" />
            <h1 className="mb-2 font-display text-2xl font-semibold">{label} Converter</h1>
            <p className="mx-auto mb-6 max-w-md text-sm text-muted">
              {label} is not part of our supported conversion graph, so there is nothing to list
              here yet. Try the converter with a different format, or browse the format catalog.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link to="/convert">
                <Button>
                  Open the converter <ArrowRight size={16} />
                </Button>
              </Link>
              <Link to="/#format-catalog">
                <Button variant="secondary">Browse the catalog</Button>
              </Link>
            </div>
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
            {category} format
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            {label} Converter
          </h1>
          <p className="mt-3 max-w-2xl text-muted">{description}</p>
        </Reveal>

        <Reveal className="mt-8">
          <div className="flex flex-wrap items-center gap-8 rounded-2xl border border-outline bg-surface/60 p-6">
            <ConverterCard
              from={cardFrom}
              to={cardTo}
              onFromClick={openConverter}
              onToClick={openConverter}
            />
            <div className="flex flex-col gap-3">
              <Link to="/convert" state={cardPair}>
                <Button size="lg">
                  {cardFrom === ext ? `Convert a ${label} file` : `Convert to ${label}`}{" "}
                  <ArrowRight size={18} />
                </Button>
              </Link>
              <p className="max-w-xs text-xs text-muted">
                Files are processed on this deployment and can be downloaded as soon as the job
                finishes.
              </p>
            </div>
          </div>
        </Reveal>

        {/* Format definition */}
        <Reveal className="mt-10">
          <div className="flex items-start gap-4 rounded-xl border border-outline bg-surface p-5">
            <FormatThumb format={ext} size="lg" label="" />
            <div>
              <h2 className="mb-1 flex items-center gap-2 font-display text-lg font-semibold">
                <Icon size={18} weight="duotone" style={{ color }} aria-hidden />
                {label} – {category} format
              </h2>
              <p className="text-sm text-muted">
                {targets.length > 0
                  ? `${label} is ${article(category)} ${category} format. Our converter accepts it as an input and can produce ${plural(targets.length, "output format")}${
                      sources.length > 0
                        ? `, and ${plural(sources.length, "format")} can be converted into it`
                        : ""
                    }.`
                  : `${label} is ${article(category)} ${category} format. Our converter can produce it from ${plural(sources.length, "input format")}.`}
              </p>
            </div>
          </div>
        </Reveal>

        {/* Convert from this format */}
        <section className="mt-12">
          <h2 className="mb-4 font-display text-xl font-semibold">Convert from {label}</h2>
          {targets.length > 0 ? (
            <FormatLinkGrid>
              {targets.map((target) => (
                <FormatLink
                  key={target}
                  to={`/${ext}-to-${target}`}
                  format={target}
                >{`${label} to ${formatVisual(target).label}`}</FormatLink>
              ))}
            </FormatLinkGrid>
          ) : (
            <p className="rounded-xl border border-outline bg-surface p-5 text-sm text-muted">
              {label} is currently only supported as a conversion target — we don't yet convert
              away from it.
            </p>
          )}
        </section>

        {/* Convert into this format */}
        <section className="mt-12">
          <h2 className="mb-4 font-display text-xl font-semibold">Convert to {label}</h2>
          {sources.length > 0 ? (
            <FormatLinkGrid>
              {sources.map((source) => (
                <FormatLink
                  key={source}
                  to={`/${source}-to-${ext}`}
                  format={source}
                >{`${formatVisual(source).label} to ${label}`}</FormatLink>
              ))}
            </FormatLinkGrid>
          ) : (
            <p className="rounded-xl border border-outline bg-surface p-5 text-sm text-muted">
              Nothing in our conversion graph produces {label} yet — it can only be used as an
              input.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
