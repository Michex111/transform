// The `@` mention grammar.
//
// Two failure modes matter, and neither is visible in a screenshot:
//
//   * a mention that fails to open, so the user's `@resume` silently does
//     nothing; and
//   * a mention that opens somewhere it should not — inside an email address,
//     for instance — where it then swallows Enter and stops the message from
//     sending at all.

import { describe, expect, it } from "vitest";
import { mentionQueryAt, stripMention } from "@/lib/mention";

describe("mentionQueryAt", () => {
  it("opens on a bare @", () => {
    // This is how the picker is reached before anything is typed.
    expect(mentionQueryAt("@", 1)).toEqual({ query: "", start: 0, end: 1 });
  });

  it("reads the query from the text after the @", () => {
    expect(mentionQueryAt("@res", 4)).toEqual({ query: "res", start: 0, end: 4 });
  });

  it("finds a mention in the middle of a sentence", () => {
    const text = "Convert @resume to PDF";
    // Caret after "resume" (index 15).
    expect(mentionQueryAt(text, 15)).toEqual({ query: "resume", start: 8, end: 15 });
  });

  it("finds a mention that starts after a newline", () => {
    const text = "Convert\n@res";
    // "@res" runs 8..12; the caret sits at the end of it.
    expect(mentionQueryAt(text, 12)).toEqual({ query: "res", start: 8, end: 12 });
  });

  it("does not open inside an email address", () => {
    // `@` preceded by a non-space is part of a word, not a mention trigger.
    expect(mentionQueryAt("foo@bar", 7)).toBeNull();
    expect(mentionQueryAt("mail me at ada@example.com", 24)).toBeNull();
  });

  it("does not open once whitespace has ended the mention", () => {
    // The user is back to ordinary prose; the picker must get out of the way.
    expect(mentionQueryAt("@resume and", 11)).toBeNull();
  });

  it("closes when the caret sits just after the trigger word", () => {
    // Space typed at the end: the mention is over.
    expect(mentionQueryAt("@resume ", 8)).toBeNull();
  });

  it("returns null when there is no @ before the caret", () => {
    expect(mentionQueryAt("hello", 5)).toBeNull();
    expect(mentionQueryAt("", 0)).toBeNull();
  });

  it("returns null for a caret outside the text", () => {
    // The caret can briefly report a stale offset (a selection collapsed after
    // a programmatic value change); that must not throw or claim a mention.
    expect(mentionQueryAt("@res", 99)).toBeNull();
    expect(mentionQueryAt("@res", -1)).toBeNull();
    expect(mentionQueryAt("@res", 1.5)).toBeNull();
  });

  it("tracks the caret rather than the end of the text", () => {
    const text = "@resume";
    // Caret mid-word: the query is only what has been typed so far.
    expect(mentionQueryAt(text, 3)).toEqual({ query: "re", start: 0, end: 3 });
  });
});

describe("stripMention", () => {
  it("removes the token without doubling the surrounding space", () => {
    const text = "Convert @resume to PDF";
    const mention = mentionQueryAt(text, 15)!;
    expect(stripMention(text, mention)).toBe("Convert to PDF");
  });

  it("removes a leading mention entirely", () => {
    const text = "@resume";
    const mention = mentionQueryAt(text, 7)!;
    expect(stripMention(text, mention)).toBe("");
  });

  it("leaves the rest of the draft untouched", () => {
    const text = "Summarize @a.pdf and @b.pdf for me";
    // Caret at the end of the first mention ("@a.pdf" spans 10..16).
    const first = mentionQueryAt(text, 16)!;
    // Only the chosen token goes; the other mention is still being typed.
    expect(stripMention(text, first)).toBe("Summarize and @b.pdf for me");
  });
});
