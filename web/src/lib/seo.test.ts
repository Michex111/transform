import { describe, expect, it } from "vitest";

import {
  breadcrumbJsonLd,
  buildHeadTags,
  faqJsonLd,
  organizationJsonLd,
  pageTitle,
  softwareApplicationJsonLd,
  tagKey,
  websiteJsonLd,
  type PageMeta,
} from "@/lib/seo";
import { DEFAULT_OG_IMAGE, SITE_ORIGIN, canonicalUrl } from "@/lib/siteMeta";

const BASE: PageMeta = { title: "Pricing", path: "/pricing" };

/** Read a tag's content by the key the head layer upserts on. */
function contentFor(meta: PageMeta, key: string): string | undefined {
  const tag = buildHeadTags(meta).find((t) => tagKey(t) === key);
  return tag && "content" in tag ? tag.content : undefined;
}

describe("pageTitle", () => {
  it("appends the site name when it is missing", () => {
    expect(pageTitle("Pricing")).toBe("Pricing · Transform");
  });

  it("does not double the site name", () => {
    expect(pageTitle("Transform — Pricing")).toBe("Transform — Pricing");
  });
});

describe("canonicalUrl", () => {
  it("prefixes the apex origin", () => {
    expect(canonicalUrl("/pricing")).toBe(`${SITE_ORIGIN}/pricing`);
  });

  it("collapses a trailing slash so both forms share one canonical", () => {
    expect(canonicalUrl("/pricing/")).toBe(`${SITE_ORIGIN}/pricing`);
  });

  it("keeps the bare root intact", () => {
    expect(canonicalUrl("/")).toBe(`${SITE_ORIGIN}/`);
  });

  it("adds a leading slash when one is missing", () => {
    expect(canonicalUrl("pricing")).toBe(`${SITE_ORIGIN}/pricing`);
  });
});

describe("buildHeadTags", () => {
  it("emits exactly one tag per identity", () => {
    const keys = buildHeadTags(BASE).map(tagKey);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("uses a descriptive title with the site suffix", () => {
    const tags = buildHeadTags(BASE);
    const title = tags.find((t) => t.kind === "title");
    expect(title && "text" in title ? title.text : "").toBe("Pricing · Transform");
  });

  it("falls back to the site description when the page omits one", () => {
    expect(contentFor(BASE, "meta:name:description")).toMatch(/AI-powered document platform/);
  });

  it("uses the page description when given", () => {
    expect(contentFor({ ...BASE, description: "Custom." }, "meta:name:description")).toBe(
      "Custom.",
    );
  });

  it("defaults Open Graph to a website with the site card", () => {
    expect(contentFor(BASE, "meta:property:og:type")).toBe("website");
    expect(contentFor(BASE, "meta:property:og:image")).toBe(DEFAULT_OG_IMAGE);
  });

  it("points the canonical and og:url at the same absolute URL", () => {
    const tags = buildHeadTags(BASE);
    const canonical = tags.find((t) => t.kind === "link");
    expect(canonical && "href" in canonical ? canonical.href : "").toBe(
      contentFor(BASE, "meta:property:og:url"),
    );
  });

  it("carries no robots directive by default", () => {
    expect(contentFor(BASE, "meta:name:robots")).toBeUndefined();
  });

  it("adds a noindex directive when asked", () => {
    expect(contentFor({ ...BASE, noindex: true }, "meta:name:robots")).toBe("noindex, nofollow");
  });
});

describe("JSON-LD builders", () => {
  it("describes the website and organisation", () => {
    expect(websiteJsonLd()).toMatchObject({ "@type": "WebSite", url: SITE_ORIGIN });
    expect(organizationJsonLd()).toMatchObject({ "@type": "Organization", name: "Transform" });
  });

  it("describes the product as a web application", () => {
    expect(softwareApplicationJsonLd("Convert files.")).toMatchObject({
      "@type": "SoftwareApplication",
      applicationCategory: "UtilitiesApplication",
      description: "Convert files.",
    });
  });

  it("maps FAQ items into Question/Answer entities", () => {
    const ld = faqJsonLd([{ question: "Is it free?", answer: "Yes, with limits." }]);
    const entity = (ld.mainEntity as Record<string, unknown>[])[0];
    expect(entity).toMatchObject({
      "@type": "Question",
      name: "Is it free?",
      acceptedAnswer: { "@type": "Answer", text: "Yes, with limits." },
    });
  });

  it("numbers breadcrumb entries from one", () => {
    const ld = breadcrumbJsonLd([
      { name: "Home", path: "/" },
      { name: "Pricing", path: "/pricing" },
    ]);
    const items = ld.itemListElement as Record<string, unknown>[];
    expect(items.map((i) => i.position)).toEqual([1, 2]);
    expect(items[1].item).toBe(`${SITE_ORIGIN}/pricing`);
  });
});
