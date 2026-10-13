import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Dropdown } from "@/components/Dropdown";
import { dropdownIdBase } from "@/lib/dropdownId";

/**
 * The app's dropdown primitive.
 *
 * Rendered with `renderToString` because this project's vitest environment is
 * `node` (no jsdom). A closed dropdown renders only its trigger, so these cover
 * the trigger's contract — the part a keyboard or screen-reader user meets before
 * anything opens — plus the pure id rule. Opening, arrow keys and the outside
 * click are the component's own state machine and are out of reach here; that
 * limit is stated rather than papered over with a fake DOM.
 */

const OPTIONS = [
  { value: "small", label: "Small" },
  { value: "large", label: "Large" },
];

function render(props: Parameters<typeof Dropdown>[0]) {
  return renderToString(<Dropdown {...props} />);
}

describe("Dropdown", () => {
  it("exposes a labelled listbox trigger, closed by default", () => {
    const html = render({
      value: "small",
      onChange: () => {},
      options: OPTIONS,
      ariaLabel: "Resource profile",
    });
    // The accessible name, and the promise that a popup exists.
    expect(html).toContain('aria-label="Resource profile"');
    expect(html).toContain('aria-haspopup="listbox"');
    expect(html).toContain('aria-expanded="false"');
    // The trigger is a real button, so it is tabbable and keyboard-openable.
    expect(html).toContain("<button");
  });

  it("shows the selected option's label rather than its raw value", () => {
    const html = render({
      value: "large",
      onChange: () => {},
      // A value that is not its own label is the normal case here — the
      // execution modes are SCRIPT/COMMAND/PROJECT but read as sentences.
      options: [{ value: "large", label: "Large (4 CPU)" }],
    });
    expect(html).toContain("Large (4 CPU)");
    expect(html).not.toContain(">large<");
  });

  it("falls back to the raw value when nothing matches", () => {
    // Better to show something the user can reason about than an empty control.
    const html = render({ value: "ghost", onChange: () => {}, options: OPTIONS });
    expect(html).toContain("ghost");
  });

  it("forwards aria-describedby to the trigger, which is what takes focus", () => {
    // The hint is the whole explanation of what the control does, so it has to
    // reach the element a screen reader announces — not a stray sibling.
    const html = render({
      value: "small",
      onChange: () => {},
      options: OPTIONS,
      ariaLabel: "Workspace",
      ariaDescribedBy: "workspace-hint",
    });
    expect(html).toContain('aria-describedby="workspace-hint"');
  });

  it("omits aria-describedby entirely when there is nothing to point at", () => {
    const html = render({ value: "small", onChange: () => {}, options: OPTIONS });
    expect(html).not.toContain("aria-describedby");
  });

  it("keeps the design system's shape and tokens in both sizes", () => {
    const md = render({ value: "small", onChange: () => {}, options: OPTIONS });
    const sm = render({
      value: "small",
      onChange: () => {},
      options: OPTIONS,
      size: "sm",
    });
    for (const html of [md, sm]) {
      // 8px radius and the same border/background tokens as every other control.
      expect(html).toContain("rounded-lg");
      expect(html).toContain("border-outline-strong");
      expect(html).toContain("bg-surface-variant");
      expect(html).toContain("focus:border-primary");
    }
    // Only the height differs, and `sm` matches `Button`'s `sm`.
    expect(md).toContain("h-9");
    expect(sm).toContain("h-8");
    expect(sm).not.toContain("h-9");
  });

  it("marks itself disabled rather than merely looking dim", () => {
    const html = render({
      value: "small",
      onChange: () => {},
      options: OPTIONS,
      disabled: true,
    });
    expect(html).toContain("disabled");
    expect(html).toContain("disabled:opacity-50");
  });
});

describe("dropdownIdBase", () => {
  it("produces an id with no whitespace", () => {
    // The regression this exists for: the id was built from the aria-label
    // verbatim, so "Resource profile" yielded `Resource profile-opt-standard` —
    // an id containing a space, which is not addressable and is invalid HTML.
    const id = dropdownIdBase("Resource profile", ":r1:");
    expect(id).not.toMatch(/\s/);
    expect(id).toBe("dropdown-resource-profile-r1");
  });

  it("strips the colons `useId()` returns, which break CSS selectors", () => {
    expect(dropdownIdBase("Mode", ":r7:")).not.toContain(":");
  });

  it("stays unique per instance when two dropdowns share a label", () => {
    const a = dropdownIdBase("Workspace", ":r1:");
    const b = dropdownIdBase("Workspace", ":r2:");
    expect(a).not.toBe(b);
  });

  it("still yields a usable id when there is no label at all", () => {
    expect(dropdownIdBase(undefined, ":r3:")).toBe("dropdown-control-r3");
  });
});
