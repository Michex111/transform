/**
 * Pure SEO helpers.
 *
 * Every function here is a plain value-in / value-out transform so it can be
 * unit-tested under the repo's `environment: "node"` Vitest setup, which has no
 * DOM. `SeoHead` is the thin DOM layer that applies what these functions build.
 *
 * The split is deliberate: "what should the head say for this page" is a
 * decision worth testing; "write it into `document.head`" is not.
 */

import {
  DEFAULT_DESCRIPTION,
  DEFAULT_OG_IMAGE,
  SITE_NAME,
  SITE_ORIGIN,
  canonicalUrl,
} from "@/lib/siteMeta";

/** A single tag the head layer should upsert. */
export type HeadTag =
  | { kind: "title"; text: string }
  | { kind: "meta"; name?: string; property?: string; content: string }
  | { kind: "link"; rel: string; href: string };

export interface PageMeta {
  /** Page-specific title, without the site suffix — the builder adds it. */
  title: string;
  /** Page-specific description. Falls back to the site default when omitted. */
  description?: string;
  /** Client-side path used to derive the canonical URL. */
  path: string;
  /** Open Graph object type. Defaults to `website`. */
  type?: "website" | "article";
  /** Absolute URL of the social image. Defaults to the site card. */
  image?: string;
  /** Ask crawlers not to index this page (auth, thin, or duplicate pages). */
  noindex?: boolean;
}

/** Title with the site suffix, unless the page title already contains it. */
export function pageTitle(title: string): string {
  return title.includes(SITE_NAME) ? title : `${title} · ${SITE_NAME}`;
}

/**
 * Build the ordered list of head tags for a page.
 *
 * Order matters only for readability; the head layer upserts each tag by its
 * identity (title, `name=`, `property=`, or `rel=`) so a re-render updates the
 * existing element instead of appending a duplicate.
 */
export function buildHeadTags(meta: PageMeta): HeadTag[] {
  const title = pageTitle(meta.title);
  const description = meta.description ?? DEFAULT_DESCRIPTION;
  const url = canonicalUrl(meta.path);
  const image = meta.image ?? DEFAULT_OG_IMAGE;
  const type = meta.type ?? "website";

  const tags: HeadTag[] = [
    { kind: "title", text: title },
    { kind: "link", rel: "canonical", href: url },
    { kind: "meta", name: "description", content: description },
    { kind: "meta", property: "og:site_name", content: SITE_NAME },
    { kind: "meta", property: "og:type", content: type },
    { kind: "meta", property: "og:title", content: title },
    { kind: "meta", property: "og:description", content: description },
    { kind: "meta", property: "og:url", content: url },
    { kind: "meta", property: "og:image", content: image },
    { kind: "meta", name: "twitter:card", content: "summary_large_image" },
    { kind: "meta", name: "twitter:title", content: title },
    { kind: "meta", name: "twitter:description", content: description },
    { kind: "meta", name: "twitter:image", content: image },
  ];

  if (meta.noindex) {
    tags.push({ kind: "meta", name: "robots", content: "noindex, nofollow" });
  }

  return tags;
}

/** A stable identity string for a tag, used to upsert without duplicates. */
export function tagKey(tag: HeadTag): string {
  switch (tag.kind) {
    case "title":
      return "title";
    case "link":
      return `link:${tag.rel}`;
    default:
      return tag.name ? `meta:name:${tag.name}` : `meta:property:${tag.property ?? ""}`;
  }
}

/* ------------------------------------------------------------------ */
/* JSON-LD builders                                                    */
/* ------------------------------------------------------------------ */

type JsonLd = Record<string, unknown>;

/** The organisation that publishes the site. */
export function organizationJsonLd(): JsonLd {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: SITE_NAME,
    url: SITE_ORIGIN,
    logo: `${SITE_ORIGIN}/apple-touch-icon.png`,
  };
}

/** The site itself, so a search engine can attach a sitename to the domain. */
export function websiteJsonLd(): JsonLd {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: SITE_NAME,
    url: SITE_ORIGIN,
  };
}

/** The product, described as a web application. */
export function softwareApplicationJsonLd(description: string): JsonLd {
  return {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: SITE_NAME,
    applicationCategory: "UtilitiesApplication",
    operatingSystem: "Web",
    description,
    url: SITE_ORIGIN,
  };
}

/** A FAQ block built from the visible question/answer pairs on the page. */
export function faqJsonLd(items: { question: string; answer: string }[]): JsonLd {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: { "@type": "Answer", text: item.answer },
    })),
  };
}

/** A breadcrumb trail, from the home page to the current page. */
export function breadcrumbJsonLd(trail: { name: string; path: string }[]): JsonLd {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: trail.map((crumb, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: crumb.name,
      item: canonicalUrl(crumb.path),
    })),
  };
}
