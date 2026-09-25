// Server-render tests for the shared status pill.
//
// The pill built its background as `${color}1a`, where `color` is a
// `var(--token)` custom property — appending an alpha hex suffix to a custom
// property is invalid CSS and is dropped by the browser, so every status badge
// lost its tinted fill. `formatTint` emits a `color-mix()` instead.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { Badge, CreditsBadge, Field, StatusBadge } from "@/components/ui";

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

// `Field` gained an `error` prop so a form can show a validation message under
// the input it belongs to instead of in a generic toast. These tests pin the
// accessibility wiring, which is the part that is invisible when it is wrong:
// the input must be marked invalid AND reference the message, or a screen-reader
// user is told nothing at all.
describe("Field", () => {
  it("derives the input id from the label", () => {
    const html = renderToString(<Field label="Confirm password" />);

    expect(html).toContain('id="confirm-password"');
    expect(html).toContain('for="confirm-password"');
  });

  it("ties a hint to its input so it is announced with it", () => {
    const html = renderToString(<Field label="Username" hint="At least 3 characters." />);

    expect(html).toContain('id="username-hint"');
    expect(html).toContain("At least 3 characters.");
    expect(html).toMatch(/aria-describedby="username-hint"/);
    // A hint is advice, not a failure.
    expect(html).not.toContain('aria-invalid="true"');
    expect(html).not.toContain('role="alert"');
  });

  it("marks the input invalid and announces the error", () => {
    const html = renderToString(
      <Field label="Username" hint="At least 3 characters." error="Use at least 3 characters." />,
    );

    expect(html).toContain('aria-invalid="true"');
    // The message has to be programmatically tied to its input, not just
    // positioned below it.
    expect(html).toMatch(/aria-describedby="username-error"/);
    expect(html).toContain('id="username-error"');
    // Announced without the user having to move focus: no toast accompanies a
    // field-level failure, so this is the live region.
    expect(html).toMatch(/role="alert"/);
  });

  it("shows the error in place of the hint rather than both", () => {
    // "Use at least 3 characters." directly under "At least 3 characters."
    // reads as the form repeating itself.
    const html = renderToString(
      <Field label="Username" hint="At least 3 characters." error="Use at least 3 characters." />,
    );

    expect(html).toContain("Use at least 3 characters.");
    // The hint text is gone (the error's wording differs only in its prefix).
    expect(html).not.toContain("At least 3 characters.<");
    // Only one description line exists, so `aria-describedby` points at the one
    // that is actually there.
    expect(html).not.toContain("username-hint");
  });

  it("renders neither line when there is no hint and no error", () => {
    const html = renderToString(<Field label="Email" />);

    expect(html).not.toContain("aria-describedby");
    expect(html).not.toContain("<p");
  });

  it("still forwards native input attributes", () => {
    const html = renderToString(
      <Field label="Email" type="email" autoComplete="email" maxLength={255} required />,
    );

    expect(html).toContain('type="email"');
    // Case-insensitive: HTML attribute names are, and React's SSR emits them
    // camelCased (`autoComplete`, `maxLength`) where a browser reads them
    // lowercased. Asserting the exact case would pin a React implementation
    // detail rather than the behaviour.
    expect(html).toMatch(/autocomplete="email"/i);
    expect(html).toMatch(/maxlength="255"/i);
    expect(html).toContain("required");
  });
});
