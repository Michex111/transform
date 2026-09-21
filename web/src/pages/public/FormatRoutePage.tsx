// FormatRoutePage — resolves the single dynamic public route `/:slug`.
//
// Static routes (`/pricing`, `/security`, `/convert`, `/login`, `/register`,
// `/app/*`) are declared as literal segments in `App.tsx`, and React Router
// ranks a static segment above a dynamic one, so those keep winning. Every
// other single-segment path lands here and is either a format hub, a
// conversion page, or an honest 404.

import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowRight, WarningCircle } from "@phosphor-icons/react";
import { Button } from "@/components/ui";
import { parseFormatSlug } from "@/lib/formatRoutes";
import { FormatConverterPage } from "@/pages/public/FormatConverterPage";
import { ConversionPage } from "@/pages/public/ConversionPage";

/** A real "page not found" panel — never an empty shell. */
function NotFoundPanel({ slug }: { slug: string }) {
  return (
    <div className="format-glyph-field">
      <div className="mx-auto max-w-2xl px-4 py-20 sm:px-6">
        <div className="rounded-xl border border-outline bg-surface p-8 text-center">
          <WarningCircle size={28} className="mx-auto mb-3 text-muted" aria-hidden />
          <h1 className="mb-2 font-display text-2xl font-semibold">Page not found</h1>
          <p className="mx-auto mb-6 max-w-md text-sm text-muted">
            There is no page at{" "}
            <span className="break-all font-mono text-on-background">
              /{slug.slice(0, 96)}
            </span>
            . Format pages look like{" "}
            <span className="font-mono text-on-background">/pdf-converter</span> or{" "}
            <span className="font-mono text-on-background">/pdf-to-docx</span>.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Link to="/">
              <Button variant="secondary">Back home</Button>
            </Link>
            <Link to="/convert">
              <Button>
                Convert a file <ArrowRight size={16} />
              </Button>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export function FormatRoutePage() {
  const { slug } = useParams<{ slug: string }>();
  const route = useMemo(() => parseFormatSlug(slug), [slug]);

  switch (route.kind) {
    case "converter":
      return <FormatConverterPage ext={route.ext} />;
    case "conversion":
      return <ConversionPage from={route.from} to={route.to} />;
    default:
      return <NotFoundPanel slug={slug ?? ""} />;
  }
}
