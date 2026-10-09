/**
 * Site-wide metadata constants.
 *
 * One place for the canonical origin, the product line, and the default
 * description so a title/description pair is never retyped per page. Every
 * public page reads from here through `SeoHead`, which keeps the words in one
 * file instead of eighteen.
 *
 * The canonical origin is the apex domain. `www.transform-to.com` 301s to it
 * and the Render host serves the app without redirecting, so emitting a
 * canonical link from this value is what consolidates the duplicate-content
 * risk the README calls out.
 */

/** Canonical origin — the apex domain, no trailing slash. */
export const SITE_ORIGIN = "https://transform-to.com";

/** The product name, used in titles and structured data. */
export const SITE_NAME = "Transform";

/**
 * The positioning line. The product is a document platform, not a one-shot
 * converter, and every public headline should reinforce that.
 */
export const SITE_TAGLINE = "Convert once. Keep it secure. Let AI handle the rest.";

/**
 * Default meta description. Written for a search result snippet: what it is,
 * what it does, and why it is different — no claims that are not shipped.
 */
export const DEFAULT_DESCRIPTION =
  "Transform is an AI-powered document platform. Convert files between 60+ formats, keep them in an encrypted Drive, and let AI work with your documents — with an API and MCP for developers and agents.";

/**
 * Social preview image. A real 1200×630 card committed under `public/`
 * (generated from the brand tokens) rather than a placeholder, so an unfurl on
 * Slack/X/LinkedIn shows the product rather than a favicon.
 */
export const DEFAULT_OG_IMAGE = `${SITE_ORIGIN}/og.png`;

/** The absolute canonical URL for a client-side path. */
export function canonicalUrl(path: string): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  // Collapse a trailing slash (except for the bare root) so `/pricing/` and
  // `/pricing` resolve to one canonical string.
  const normalised = clean.length > 1 ? clean.replace(/\/+$/, "") : clean;
  return `${SITE_ORIGIN}${normalised}`;
}
