import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";

import { SeoHead } from "@/components/SeoHead";

describe("SeoHead", () => {
  it("renders nothing into the tree", () => {
    // It writes metadata straight into `document.head`, so anything it returned
    // would double-render into the page. Pinning the empty render keeps a future
    // refactor from leaking markup into layouts that place it inside a section.
    const html = renderToString(
      <SeoHead meta={{ title: "Pricing", path: "/pricing" }} jsonLd={[{ "@type": "Thing" }]} />,
    );
    expect(html).toBe("");
  });

  it("renders nothing when only a title is supplied", () => {
    expect(renderToString(<SeoHead meta={{ title: "Home", path: "/" }} />)).toBe("");
  });
});
