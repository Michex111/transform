// formatPickerOptions — the rules that decide what a `FormatPicker` may offer.
//
// Extracted from the component so the restriction rules can be unit tested
// without a DOM. The important rule is the empty case: the allowed list comes
// from the backend conversion graph, and when that graph has not arrived yet the
// list is empty. Treating an empty list as "no restriction" would offer the
// whole static catalogue — including pairs the server rejects — so an empty list
// must offer nothing instead.

import { FORMAT_CATEGORIES, type FormatCategory } from "@/lib/formatCatalog";

export type FormatCategories = typeof FORMAT_CATEGORIES;

/**
 * Narrow the category list to formats present in `allowed`.
 *
 * @param allowed Valid extensions, or `undefined` for an unrestricted picker.
 *   An empty list is a restriction with nothing in it and yields `[]`.
 */
export function restrictFormatCategories(
  allowed: readonly string[] | undefined,
  categories: FormatCategories = FORMAT_CATEGORIES,
): FormatCategories {
  if (allowed === undefined) return categories;

  const permitted = new Set(allowed.map((ext) => ext.toLowerCase()));
  const restricted: FormatCategory[] = [];
  for (const category of categories) {
    const formats = category.formats.filter((format) => permitted.has(format.ext.toLowerCase()));
    if (formats.length) restricted.push({ ...category, formats });
  }
  return restricted;
}

/**
 * Whether `ext` may be selected.
 *
 * An unrestricted picker accepts anything; a restricted one accepts only what
 * the conversion graph listed.
 */
export function isPickableFormat(allowed: readonly string[] | undefined, ext: string): boolean {
  if (allowed === undefined) return true;
  const wanted = ext.toLowerCase();
  return allowed.some((candidate) => candidate.toLowerCase() === wanted);
}
