// Server-render tests for the shared status pill.
//
// The pill built its background as `${color}1a`, where `color` is a
// `var(--token)` custom property — appending an alpha hex suffix to a custom
// property is invalid CSS and is dropped by the browser, so every status badge
// lost its tinted fill. `formatTint` emits a `color-mix()` instead.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { Badge, CreditsBadge, StatusBadge } from "@/components/ui";

describe("Badge", () => {
  it("tints its background with a real, computed colour", () => {
    const html = renderToString(<Badge color="var(--color-primary)">Converting</Badge>);
    expect(html).toContain("color-mix(");
    // The old, silently-dropped form must be gone.
    expect(html).not.toContain("var(--color-primary)1a");
    expect(html).toContain("var(--color-primary)");
  });

  it("keeps the status label and its colour", () => {
    const html = renderToString(<StatusBadge status="PROCESSING" />);
    expect(html).toContain("Converting");
    expect(html).toContain("color-mix(");
    expect(html).toContain("var(--color-primary)");
  });

  it("renders the token cost as a labelled figure", () => {
    const html = renderToString(<CreditsBadge credits={12} />);
    expect(html).toContain("12 tokens used");
  });
});
