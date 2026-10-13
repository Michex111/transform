/**
 * Id derivation for `Dropdown`'s options.
 *
 * Lives outside `Dropdown.tsx` for the same reason `navItems.ts` lives outside
 * `AppShell.tsx`: `react-refresh/only-export-components` rejects a module that
 * exports both a component and a plain function, because editing the function
 * would remount the component instead of patching it.
 *
 * Option ids have to be unique per instance AND valid. Deriving them from the
 * aria-label alone broke both: a label like "Resource profile" produced an id
 * containing a space, which is not addressable and is invalid HTML, and two
 * dropdowns sharing a label collided.
 *
 * Pure and exported so the rule can be asserted directly — the options only exist
 * in the DOM while the popup is open, which a node-environment test cannot reach.
 */
export function dropdownIdBase(ariaLabel: string | undefined, unique: string): string {
  const slug = (ariaLabel ?? "control")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .toLowerCase();
  // `useId()` contains colons, which are legal in an HTML id but break a CSS
  // selector, so they go too.
  const suffix = unique.replace(/[^a-zA-Z0-9]+/g, "");
  return `dropdown-${slug}-${suffix}`;
}
