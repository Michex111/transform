/**
 * Which section of the account settings page is open.
 *
 * The tab lives in the query string rather than in component state because the
 * shell's profile dropdown deep-links straight into a section
 * (`settingsTabHref("phone")`), and a link has to survive being pasted into
 * another tab or opened cold. Keeping the list here also means the reader and
 * the writer of that value cannot drift apart.
 */

export const SETTINGS_TABS = ["profile", "phone", "api-keys", "danger"] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

/**
 * Retired tabs that must keep resolving, mapped to the screen that replaced
 * them.
 *
 * `password` used to be a tab; it is now a card that sits beside Profile the
 * whole time, so a tab for it would render the same form twice. Old bookmarks,
 * "copy link" shares and browser history still say `?tab=password`, and a dead
 * end would be a worse answer than the screen that contains the password card.
 *
 * Spelled out rather than left to the unknown-value fallback below: the two
 * happen to agree today, but this is a deliberate promise about a URL that
 * existed, and a test pins it. Add a retired tab here when one is removed —
 * never back into `SETTINGS_TABS`, which is what the tab strip renders.
 */
export const LEGACY_SETTINGS_TABS: Readonly<Record<string, SettingsTab>> = Object.freeze({
  password: "profile",
});

/** True for a value that names a real tab (query strings are untrusted input). */
export function isSettingsTab(value: unknown): value is SettingsTab {
  return typeof value === "string" && (SETTINGS_TABS as readonly string[]).includes(value);
}

/**
 * The tab a search string asks for, defaulting to `profile`.
 *
 * Absent (`""`), empty (`"?tab="`) and unknown (`"?tab=bogus"`) all fall back
 * rather than erroring: the query string is user-editable, and a broken link
 * should land on a working page instead of a blank one. A retired tab resolves
 * through {@link LEGACY_SETTINGS_TABS} before that fallback, so
 * `?tab=password` lands on profile on purpose rather than by coincidence.
 *
 * The lookup is `hasOwnProperty` rather than a bare index: `?tab=constructor`
 * (or `toString`, or `__proto__`) would otherwise return something inherited
 * from `Object.prototype` instead of a tab name, and the page would render an
 * empty panel with a tab id nobody could select.
 */
export function readSettingsTab(search: string): SettingsTab {
  const requested = new URLSearchParams(search).get("tab");
  if (isSettingsTab(requested)) return requested;
  if (requested && Object.prototype.hasOwnProperty.call(LEGACY_SETTINGS_TABS, requested)) {
    return LEGACY_SETTINGS_TABS[requested];
  }
  return "profile";
}

/**
 * The address of a tab.
 *
 * Profile is the bare path so the default view has one canonical URL — two
 * addresses for the same page splits history and breaks "copy this link".
 * Total over the current union: a retired tab has no href of its own, so there
 * is nothing here that can emit a link the reader would not resolve.
 */
export function settingsTabHref(tab: SettingsTab): string {
  return tab === "profile" ? "/app/settings" : `/app/settings?tab=${tab}`;
}
