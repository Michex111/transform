// The persisted conversation-rail preference.
//
// `localStorage` is absent in this environment (and blocked entirely in some
// privacy modes), so the helpers take the storage object as a parameter. What is
// pinned here is the degradation rule: a preference that cannot be read or
// written must leave the UI at its default rather than break the page or hide a
// panel the user cannot explain.

import { describe, expect, it, vi } from "vitest";
import {
  RAIL_COLLAPSED_KEY,
  readRailCollapsed,
  writeRailCollapsed,
  type PreferenceStorage,
} from "@/lib/layoutPrefs";

/** An in-memory stand-in for `localStorage`. */
function memoryStorage(initial: Record<string, string> = {}): PreferenceStorage & {
  data: Record<string, string>;
} {
  const data = { ...initial };
  return {
    data,
    getItem: (key: string) => data[key] ?? null,
    setItem: (key: string, value: string) => {
      data[key] = value;
    },
  };
}

describe("readRailCollapsed", () => {
  it("defaults to expanded when nothing is stored", () => {
    // The rail is useful; a first visit must show it.
    expect(readRailCollapsed(memoryStorage())).toBe(false);
  });

  it("reads a stored collapse", () => {
    expect(readRailCollapsed(memoryStorage({ [RAIL_COLLAPSED_KEY]: "true" }))).toBe(true);
  });

  it("reads a stored expansion back as expanded", () => {
    expect(readRailCollapsed(memoryStorage({ [RAIL_COLLAPSED_KEY]: "false" }))).toBe(false);
  });

  it("falls back to expanded for a corrupted value", () => {
    // Only the exact string "true" collapses. Anything else must not hide a
    // panel the user cannot then find.
    for (const junk of ["1", "yes", "TRUE", "", "null"]) {
      expect(readRailCollapsed(memoryStorage({ [RAIL_COLLAPSED_KEY]: junk }))).toBe(false);
    }
  });

  it("survives storage that throws on read", () => {
    const hostile: PreferenceStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {},
    };
    expect(readRailCollapsed(hostile)).toBe(false);
  });

  it("survives the absence of any storage", () => {
    expect(readRailCollapsed(null)).toBe(false);
  });
});

describe("writeRailCollapsed", () => {
  it("round-trips through the reader", () => {
    const storage = memoryStorage();
    writeRailCollapsed(true, storage);
    expect(readRailCollapsed(storage)).toBe(true);
    writeRailCollapsed(false, storage);
    expect(readRailCollapsed(storage)).toBe(false);
  });

  it("never throws when storage rejects the write", () => {
    // Quota-exceeded and private-mode writes throw; a layout preference is not
    // worth surfacing that as an error.
    const hostile: PreferenceStorage = {
      getItem: () => null,
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(() => writeRailCollapsed(true, hostile)).not.toThrow();
    expect(() => writeRailCollapsed(true, null)).not.toThrow();
  });

  it("does not read on write", () => {
    const getItem = vi.fn(() => null);
    writeRailCollapsed(true, { getItem, setItem: () => {} });
    expect(getItem).not.toHaveBeenCalled();
  });
});
