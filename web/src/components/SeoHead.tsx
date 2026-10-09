import { useEffect } from "react";
import { buildHeadTags, tagKey, type HeadTag, type PageMeta } from "@/lib/seo";

/**
 * Applies per-page metadata to `<head>`.
 *
 * A Vite SPA ships one static `index.html`, so without this every route shares
 * the same title and description. This component owns the public pages' head
 * content: title, description, canonical, Open Graph, Twitter, robots, and one
 * `application/ld+json` block.
 *
 * How it stays correct across navigations
 * ---------------------------------------
 * Tags are **upserted by identity** (title / `name=` / `property=` / `rel=`), so
 * a re-render updates the existing element rather than appending a duplicate —
 * a second `<meta name="description">` would make the crawler's choice
 * arbitrary.
 *
 * Elements this component creates are tagged `data-seo`, and each instance
 * records which keys it owns in a module-level map. On unmount a key is removed
 * only if this instance is still its owner. That closes the mount/unmount race:
 * if the next page mounts first it takes ownership, and the outgoing page's
 * cleanup leaves its elements alone; if the outgoing page unmounts first it
 * removes its own elements and the next page creates fresh ones.
 *
 * The static `<title>`/`<meta name="description">` in `index.html` are never
 * removed — they are the no-JS fallback the next page overwrites.
 */
export function SeoHead({
  meta,
  jsonLd,
}: {
  meta: PageMeta;
  jsonLd?: Record<string, unknown>[];
}) {
  // Serialised dependency: a fresh `meta` object every render must not re-run
  // the effect, but any real content change must.
  const dep = JSON.stringify({ meta, jsonLd });

  useEffect(() => {
    const owner = ++ownerSeq;
    const tags = buildHeadTags(meta);
    const keys: string[] = [];

    for (const tag of tags) {
      const key = tagKey(tag);
      let el = document.head.querySelector(selectorFor(tag));
      if (!el) {
        el = createTag(tag);
        el.setAttribute("data-seo", "1");
        document.head.appendChild(el);
      }
      applyTag(el, tag);
      claims.set(key, owner);
      keys.push(key);
    }

    if (jsonLd && jsonLd.length > 0) {
      let script = document.getElementById("seo-jsonld") as HTMLScriptElement | null;
      if (!script) {
        script = document.createElement("script");
        script.type = "application/ld+json";
        script.id = "seo-jsonld";
        document.head.appendChild(script);
      }
      script.textContent = JSON.stringify(jsonLd);
    }

    return () => {
      for (const key of keys) {
        // A newer page took ownership — its elements must survive.
        if (claims.get(key) !== owner) continue;
        claims.delete(key);
        const tag = tags.find((t) => tagKey(t) === key);
        if (!tag) continue;
        const el = document.head.querySelector(selectorFor(tag));
        if (el?.getAttribute("data-seo") === "1") el.remove();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep]);

  return null;
}

let ownerSeq = 0;
const claims = new Map<string, number>();

function selectorFor(tag: HeadTag): string {
  switch (tag.kind) {
    case "title":
      return "title";
    case "link":
      return `link[rel="${tag.rel}"]`;
    default:
      return tag.name
        ? `meta[name="${tag.name}"]`
        : `meta[property="${tag.property ?? ""}"]`;
  }
}

function createTag(tag: HeadTag): Element {
  if (tag.kind === "title") return document.createElement("title");
  if (tag.kind === "link") {
    const el = document.createElement("link");
    el.setAttribute("rel", tag.rel);
    return el;
  }
  const el = document.createElement("meta");
  if (tag.name) el.setAttribute("name", tag.name);
  else el.setAttribute("property", tag.property ?? "");
  return el;
}

function applyTag(el: Element, tag: HeadTag): void {
  if (tag.kind === "title") el.textContent = tag.text;
  else if (tag.kind === "link") el.setAttribute("href", tag.href);
  else el.setAttribute("content", tag.content);
}
