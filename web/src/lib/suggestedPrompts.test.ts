// Tests for the seeded suggestion picker.
//
// The reported bug was that every new chat showed the same four examples. The
// contract is: stable for a seed, different across seeds, never duplicated, and
// every prompt in the pool is reachable.

import { describe, expect, it } from "vitest";
import {
  SUGGESTED_PROMPTS,
  USAGE_PROMPTS,
  pickMiniSuggestions,
  pickSuggestions,
} from "@/lib/suggestedPrompts";

describe("pickSuggestions", () => {
  it("is stable for the same seed", () => {
    expect(pickSuggestions("session-a")).toEqual(pickSuggestions("session-a"));
    expect(pickSuggestions(42)).toEqual(pickSuggestions(42));
  });

  it("returns the requested number of distinct prompts", () => {
    const picked = pickSuggestions("seed", 4);
    expect(picked).toHaveLength(4);
    expect(new Set(picked).size).toBe(4);
  });

  it("defaults to four", () => {
    expect(pickSuggestions("seed")).toHaveLength(4);
  });

  it("never returns duplicates for any seed", () => {
    for (let seed = 0; seed < 200; seed++) {
      const picked = pickSuggestions(seed, 4);
      expect(new Set(picked).size).toBe(picked.length);
    }
  });

  it("only ever returns prompts from the pool", () => {
    for (let seed = 0; seed < 200; seed++) {
      for (const prompt of pickSuggestions(seed, 4)) {
        expect(SUGGESTED_PROMPTS).toContain(prompt);
      }
    }
  });

  it("spreads the first pick across many distinct prompts", () => {
    const firsts = new Set<string>();
    for (let seed = 0; seed < 100; seed++) {
      firsts.add(pickSuggestions(seed, 4)[0]);
    }
    // A fixed list would give exactly one; the point of the bug fix is variety.
    expect(firsts.size).toBeGreaterThanOrEqual(3);
  });

  it("can reach every pool entry as a first pick", () => {
    const firsts = new Set<string>();
    for (let seed = 0; seed < 5000; seed++) {
      firsts.add(pickSuggestions(seed, 4)[0]);
    }
    expect(firsts.size).toBe(SUGGESTED_PROMPTS.length);
  });

  it("clamps the count to the pool size", () => {
    expect(pickSuggestions("seed", 999)).toHaveLength(SUGGESTED_PROMPTS.length);
  });

  it("returns nothing for a non-positive count", () => {
    expect(pickSuggestions("seed", 0)).toEqual([]);
    expect(pickSuggestions("seed", -3)).toEqual([]);
  });

  it("does not mutate the pool", () => {
    const before = [...SUGGESTED_PROMPTS];
    pickSuggestions("seed", 4);
    expect([...SUGGESTED_PROMPTS]).toEqual(before);
  });
});

describe("pickMiniSuggestions", () => {
  it("always leads with a usage/credits question", () => {
    for (let seed = 0; seed < 100; seed++) {
      const picked = pickMiniSuggestions(seed, 3);
      expect(USAGE_PROMPTS).toContain(picked[0]);
    }
  });

  it("returns the requested number of distinct prompts from the pool", () => {
    const picked = pickMiniSuggestions("mini-seed", 3);
    expect(picked).toHaveLength(3);
    expect(new Set(picked).size).toBe(3);
    for (const prompt of picked) expect(SUGGESTED_PROMPTS).toContain(prompt);
  });

  it("is stable for the same seed", () => {
    expect(pickMiniSuggestions(7, 3)).toEqual(pickMiniSuggestions(7, 3));
  });

  it("clamps a non-positive count to a single usage question", () => {
    expect(pickMiniSuggestions("seed", 0)).toHaveLength(1);
  });
});
