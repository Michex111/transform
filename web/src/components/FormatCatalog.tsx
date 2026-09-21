// FormatCatalog — the public "Format Catalog" article section.
//
// Unlike the static `FORMAT_CATEGORIES` catalog, every number and every link
// here is derived from the live conversion graph (`useConversionMap`), so the
// catalogue can never advertise a format or a pair the backend cannot actually
// convert. While the graph loads we show skeletons; if it is unavailable we
// show an honest empty state instead of a grid of dead links.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "@phosphor-icons/react";
import { FORMAT_CATEGORIES } from "@/lib/formatCatalog";
import { formatTint, formatVisual } from "@/lib/formatVisual";
import { useConversionMap } from "@/lib/useConversionMap";
import { FormatThumb } from "@/components/FormatThumb";
import { FormatLink, FormatLinkGrid } from "@/components/FormatLink";
import { Button, Skeleton } from "@/components/ui";
import { Reveal } from "@/lib/motion";

/** Pairs we would like to feature, in order, when the graph supports them. */
const PREFERRED_CONVERSIONS: { from: string; to: string; caption: string }[] = [
  { from: "pdf", to: "docx", caption: "Editable Word document, ready to revise." },
  { from: "docx", to: "pdf", caption: "Print-ready PDF that renders the same everywhere." },
  { from: "png", to: "jpg", caption: "Smaller, web-friendly image for sharing." },
];

const MAX_COMMON = 3;

interface CommonConversion {
  from: string;
  to: string;
  caption: string;
}

export function FormatCatalog() {
  const { map, supported, loading, error } = useConversionMap({ guest: true });
  const [activeId, setActiveId] = useState<string | null>(null);
  // Only categories that actually contain a supported format survive, so an
  // empty category (with its `iconColor`) never reaches the pills.
  const categories = useMemo(
    () =>
      FORMAT_CATEGORIES.map((category) => ({
        ...category,
        formats: category.formats.filter((format) => supported.has(format.ext.toLowerCase())),
      })).filter((category) => category.formats.length > 0),
    [supported],
  );

  const totalFormats = useMemo(
    () => categories.reduce((sum, category) => sum + category.formats.length, 0),
    [categories],
  );

  // Open on a document category when one exists — it is the most common entry
  // point, and `FORMAT_CATEGORIES` happens to start with archive, which would
  // otherwise be the default.
  const defaultCategory =
    categories.find((category) => category.id === "document") ?? categories[0];
  const active = categories.find((category) => category.id === activeId) ?? defaultCategory;

  // Preferred pairs first; top up from the graph by target count so we always
  // render three real links whenever the graph has three edges to offer.
  const common = useMemo<CommonConversion[]>(() => {
    const chosen: CommonConversion[] = PREFERRED_CONVERSIONS.filter((pair) =>
      map[pair.from]?.includes(pair.to),
    );

    if (chosen.length < MAX_COMMON) {
      const ranked = Object.entries(map)
        .filter(([, targets]) => targets.length > 0)
        .sort((a, b) => b[1].length - a[1].length);

      for (const [source, targets] of ranked) {
        if (chosen.length >= MAX_COMMON) break;
        const target = targets[0];
        if (chosen.some((pair) => pair.from === source && pair.to === target)) continue;
        chosen.push({
          from: source,
          to: target,
          caption: `One of ${targets.length} formats ${source.toUpperCase()} converts to.`,
        });
      }
    }

    return chosen.slice(0, MAX_COMMON);
  }, [map]);

  const unavailable = !loading && (Boolean(error) || totalFormats === 0);

  return (
    <section
      id="format-catalog"
      className="mx-auto max-w-6xl scroll-mt-20 px-4 py-12 sm:px-6"
    >
      <Reveal className="mb-8 max-w-2xl">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
          Format Catalog
        </p>
        <h2 className="font-display text-2xl font-semibold sm:text-3xl">
          Every format we can move between
        </h2>
        <p className="mt-3 text-muted">
          {loading ? (
            "Reading the live conversion graph…"
          ) : unavailable ? (
            "The conversion graph is unavailable right now, so this catalogue is temporarily empty."
          ) : (
            <>
              <strong className="font-semibold text-on-background">{totalFormats} formats</strong>{" "}
              across {categories.length}{" "}
              {categories.length === 1 ? "category" : "categories"}. Pick a format to see what it
              converts to — no account required.
            </>
          )}
        </p>
      </Reveal>

      {loading && (
        <>
          <div className="mb-6 flex flex-wrap gap-2" aria-hidden>
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-28 rounded-full" />
            ))}
          </div>
          <div className="mb-8 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" aria-hidden>
            {Array.from({ length: 12 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-lg" />
            ))}
          </div>
          <div className="flex items-center justify-center py-6 text-sm text-muted" role="status">
            Loading formats…
          </div>
        </>
      )}

      {unavailable && (
        <div className="rounded-xl border border-outline bg-surface p-8 text-center">
          <h3 className="mb-2 font-display text-lg font-semibold">Formats are unavailable</h3>
          <p className="mx-auto mb-5 max-w-md text-sm text-muted">
            We could not load the list of supported conversions. You can still open the converter
            and try a file — it will tell you what it can turn it into.
          </p>
          <Link to="/convert">
            <Button variant="secondary">
              Open the converter <ArrowRight size={16} />
            </Button>
          </Link>
        </div>
      )}

      {!loading && !unavailable && active && (
        <>
          {/* Category pills with live counts */}
          <div className="mb-8 flex flex-wrap gap-2">
            {categories.map((category) => {
              const isActive = category.id === active.id;
              return (
                <button
                  key={category.id}
                  type="button"
                  aria-pressed={isActive}
                  onClick={() => setActiveId(category.id)}
                  className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? "text-on-background"
                      : "border-outline-strong text-muted hover:text-on-background"
                  }`}
                  style={
                    isActive
                      ? {
                          borderColor: formatTint(category.iconColor, 55),
                          backgroundColor: formatTint(category.iconColor, 16),
                        }
                      : undefined
                  }
                >
                  {category.name}
                  <span className="rounded-full bg-surface-variant px-1.5 py-0.5 font-mono text-[11px] text-muted">
                    {category.formats.length}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Formats in the selected category */}
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-display text-lg font-semibold">{active.name} formats</h3>
            <span className="font-mono text-xs text-muted">
              {active.formats.length} listed
            </span>
          </div>
          <FormatLinkGrid>
            {active.formats.map((format) => (
              <FormatLink key={format.ext} to={`/${format.ext}-converter`} format={format.ext} />
            ))}
          </FormatLinkGrid>

          {/* Common conversion types */}
          {common.length > 0 && (
            <div className="mt-12">
              <h3 className="mb-4 font-display text-lg font-semibold">Common conversion types</h3>
              <div className="grid gap-3 sm:grid-cols-3">
                {common.map((pair) => {
                  const fromVisual = formatVisual(pair.from);
                  const toVisual = formatVisual(pair.to);
                  return (
                    <Link
                      key={`${pair.from}-${pair.to}`}
                      to={`/${pair.from}-to-${pair.to}`}
                      className="flex flex-col gap-3 rounded-xl border border-outline bg-surface p-4 transition-colors hover:border-primary/40"
                    >
                      <span className="flex items-center gap-2">
                        <FormatThumb format={pair.from} size="sm" label="" />
                        <ArrowRight size={14} className="shrink-0 text-muted" aria-hidden />
                        <FormatThumb format={pair.to} size="sm" label="" />
                      </span>
                      <span className="font-display text-sm font-semibold">
                        {fromVisual.label} to {toVisual.label}
                      </span>
                      <span className="text-xs text-muted">{pair.caption}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
