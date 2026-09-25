// The tab a settings URL asks for. The query string is user-editable and is
// also written by the profile dropdown, so every malformed shape has to land
// somewhere sane.

import { describe, expect, it } from "vitest";
import {
  LEGACY_SETTINGS_TABS,
  SETTINGS_TABS,
  isSettingsTab,
  readSettingsTab,
  settingsTabHref,
} from "@/pages/app/settings/settingsTabs";

describe("readSettingsTab", () => {
  it("defaults to profile when nothing is asked for", () => {
    expect(readSettingsTab("")).toBe("profile");
    expect(readSettingsTab("?")).toBe("profile");
    expect(readSettingsTab("?other=1")).toBe("profile");
  });

  it("defaults to profile for an empty tab value", () => {
    expect(readSettingsTab("?tab=")).toBe("profile");
  });

  it("defaults to profile for an unknown tab", () => {
    expect(readSettingsTab("?tab=bogus")).toBe("profile");
    // Close to a real value, which is exactly what a typo looks like.
    expect(readSettingsTab("?tab=apikeys")).toBe("profile");
  });

  it("reads every real tab, with or without the leading question mark", () => {
    for (const tab of SETTINGS_TABS) {
      expect(readSettingsTab(`?tab=${tab}`)).toBe(tab);
      expect(readSettingsTab(`tab=${tab}`)).toBe(tab);
      expect(readSettingsTab(`?foo=1&tab=${tab}`)).toBe(tab);
    }
  });

  it("ignores a tab that is repeated", () => {
    // `URLSearchParams.get` returns the first value; the page must not flip
    // depending on how many times a link was pasted.
    expect(readSettingsTab("?tab=phone&tab=danger")).toBe("phone");
  });
});

describe("isSettingsTab", () => {
  it("accepts the real tabs", () => {
    for (const tab of SETTINGS_TABS) expect(isSettingsTab(tab)).toBe(true);
  });

  it("rejects everything else", () => {
    expect(isSettingsTab("")).toBe(false);
    expect(isSettingsTab("Profile")).toBe(false);
    expect(isSettingsTab(null)).toBe(false);
    expect(isSettingsTab(undefined)).toBe(false);
    expect(isSettingsTab(3)).toBe(false);
  });
});

describe("the retired password tab", () => {
  it("is not a tab any more", () => {
    // The password card sits beside Profile permanently, so a tab for it would
    // render the same form twice. It must not be in the strip.
    expect(SETTINGS_TABS).not.toContain("password");
    expect(isSettingsTab("password")).toBe(false);
  });

  it("still resolves, to the screen that contains the password card", () => {
    // Explicit rather than an accident of the unknown-value fallback: an old
    // bookmark or a pasted link has to land on profile on purpose.
    expect(LEGACY_SETTINGS_TABS.password).toBe("profile");
    expect(readSettingsTab("?tab=password")).toBe("profile");
    expect(readSettingsTab("?tab=password&foo=1")).toBe("profile");
  });

  it("does not resolve a key inherited from Object.prototype", () => {
    // The map is an object, so a bare index would answer `?tab=constructor`
    // with a function instead of a tab name.
    for (const key of ["constructor", "toString", "__proto__", "valueOf"]) {
      expect(readSettingsTab(`?tab=${key}`)).toBe("profile");
    }
  });
});

describe("settingsTabHref", () => {
  it("gives profile the bare path, so there is one canonical URL", () => {
    expect(settingsTabHref("profile")).toBe("/app/settings");
  });

  it("names every other tab in the query string", () => {
    expect(settingsTabHref("phone")).toBe("/app/settings?tab=phone");
    expect(settingsTabHref("api-keys")).toBe("/app/settings?tab=api-keys");
    expect(settingsTabHref("danger")).toBe("/app/settings?tab=danger");
  });

  it("covers every tab in the strip", () => {
    // Total over the union: a tab with no case here would be a link the reader
    // could not resolve.
    for (const tab of SETTINGS_TABS) expect(settingsTabHref(tab)).toContain("/app/settings");
    expect(SETTINGS_TABS).toHaveLength(4);
  });

  it("round-trips through the reader", () => {
    for (const tab of SETTINGS_TABS) {
      const href = settingsTabHref(tab);
      expect(readSettingsTab(href.slice(href.indexOf("?")))).toBe(tab);
    }
  });
});
