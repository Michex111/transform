// Slug parsing for the public `/:slug` format routes.
//
// Kept out of the page component so the rules are unit-testable and so the
// route file only exports a component (React Fast Refresh).
//
// Two shapes are produced:
//   "{ext}-converter"  → the format hub,  e.g. `/pdf-converter`
//   "{from}-to-{to}"   → a conversion,    e.g. `/pdf-to-docx`
// Anything else is a genuine 404 rather than an empty shell.

export type FormatRoute =
  | { kind: "converter"; ext: string }
  | { kind: "conversion"; from: string; to: string }
  | { kind: "not-found" };

/** Hard ceiling on the whole slug, so absurd URLs never reach a page. */
const MAX_SLUG_LENGTH = 96;
/** Longest extension we accept (`tar.bz2` is 7). */
const MAX_EXT_LENGTH = 12;

/** Whole slug: lowercase letters, digits, dots and the `-converter`/`-to-` separators. */
const SAFE_SLUG = /^[a-z0-9.-]+$/;
/** One extension token: dot-separated alphanumeric segments (`tar.bz2`, `7z`, `pdf`). */
const EXT_TOKEN = /^[a-z0-9]+(?:\.[a-z0-9]+)*$/;

const CONVERTER_SUFFIX = "-converter";
const CONVERSION_MARKER = "-to-";

/** Validate and return a single extension segment, or null when unsafe. */
function normalizeExtToken(token: string): string | null {
  if (!token || token.length > MAX_EXT_LENGTH) return null;
  if (!EXT_TOKEN.test(token)) return null;
  return token;
}

/**
 * Parse a `/:slug` path segment into a route decision.
 *
 * Returns `{ kind: "not-found" }` for anything malformed — over-long segments,
 * characters outside `[a-z0-9.]`, doubled separators, or a suffix with no
 * usable extension — so the caller can render a real 404 panel.
 */
export function parseFormatSlug(rawSlug: string | undefined): FormatRoute {
  const slug = (rawSlug ?? "").toLowerCase();
  if (!slug || slug.length > MAX_SLUG_LENGTH) return { kind: "not-found" };
  if (!SAFE_SLUG.test(slug)) return { kind: "not-found" };
  // Reject stray/doubled separators before they can be interpreted as an ext.
  if (slug.startsWith("-") || slug.endsWith("-") || slug.includes("..")) {
    return { kind: "not-found" };
  }

  if (slug.endsWith(CONVERTER_SUFFIX)) {
    const ext = normalizeExtToken(slug.slice(0, -CONVERTER_SUFFIX.length));
    return ext ? { kind: "converter", ext } : { kind: "not-found" };
  }

  const splitAt = slug.indexOf(CONVERSION_MARKER);
  if (splitAt > 0) {
    const from = normalizeExtToken(slug.slice(0, splitAt));
    // Everything after the FIRST marker must itself be a single ext token, so
    // `a-to-b-to-c` is rejected instead of parsed as a two-hop chain.
    const to = normalizeExtToken(slug.slice(splitAt + CONVERSION_MARKER.length));
    if (from && to) return { kind: "conversion", from, to };
  }

  return { kind: "not-found" };
}
