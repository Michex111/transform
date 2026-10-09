// Which target formats a workflow editor may offer.
//
// Pulled out of the page because it is the rule that decides what a user is
// *allowed to save into a workflow*, and getting it wrong is quiet: an
// over-narrow list silently hides formats, and a mis-cased value makes the
// picker render as disabled for a workflow that is perfectly valid.
//
// The list is the conversion graph's own targets — not the static catalogue —
// so the picker cannot offer a format the server would then reject. The
// catalogue is verified to be a superset of the graph, so nothing valid is
// hidden by intersecting the two.

/**
 * Normalise the conversion map's target list into the `allowed` list a format
 * picker expects.
 *
 * Lowercased and de-duplicated. Both matter:
 *
 *   - **lowercased**, because the picker matches the currently selected value
 *     against this list with an exact comparison. A workflow whose stored
 *     `target_format` is `"PDF"` — a row written by an older revision, or edited
 *     directly in the database — would otherwise render the picker as disabled
 *     for a conversion that is entirely valid.
 *   - **de-duplicated**, defensively. `useConversionMap.targets` already
 *     de-duplicates, but this is a boundary between two modules and the cost of
 *     not re-checking is a picker with duplicated entries.
 *
 * Deliberately NOT sorted: the picker groups by the catalogue's categories, so
 * the input order has no effect on layout, and sorting here would imply an
 * ordering this list does not actually control.
 */
export function workflowTargetOptions(targets: readonly string[]): string[] {
  const unique = new Set<string>();
  for (const target of targets) {
    const normalised = target.trim().toLowerCase();
    if (normalised) unique.add(normalised);
  }
  return Array.from(unique);
}
