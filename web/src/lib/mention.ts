// The `@` mention grammar for the composer's document picker.
//
// Pure and DOM-free so the trigger rule is pinned by tests rather than tuned by
// feel in a textarea: getting it wrong either fails to open the picker for a
// real mention, or — worse — opens it inside an email address the user is
// typing and swallows their Enter key.

/** A live `@` mention immediately before the caret. */
export interface MentionQuery {
  /** The text typed after the `@`, up to the caret. */
  query: string;
  /** Index of the `@` itself. */
  start: number;
  /** Index just past the caret — the end of the text to replace on selection. */
  end: number;
}

/** Characters that end a mention. A mention is a single word. */
const TERMINATOR = /\s/;

/**
 * The `@` mention the caret is currently inside, or `null`.
 *
 * A mention is triggered by an `@` that starts a word — at the beginning of the
 * input, or preceded by whitespace — and runs unbroken to the caret. Anything
 * else is just an `@` character:
 *
 *   - `"foo@bar"` — the `@` follows a non-space, so it is an email address (or
 *     at least not a mention) and the picker must not open under the user's
 *     typing;
 *   - `"@res u"` — the space ended the mention, so the caret is in ordinary
 *     prose again.
 *
 * An empty query (`"@"` with the caret right after) is still a mention: that is
 * how the picker is opened before anything has been typed.
 */
export function mentionQueryAt(text: string, caret: number): MentionQuery | null {
  if (!Number.isInteger(caret) || caret < 0 || caret > text.length) return null;

  // Walk back from the caret to the start of the word, or a terminator.
  let index = caret - 1;
  while (index >= 0) {
    const char = text[index];
    if (char === "@") {
      const before = index > 0 ? text[index - 1] : "";
      // An `@` that does not start the word is not ours to claim.
      if (before && !TERMINATOR.test(before)) return null;
      return { query: text.slice(index + 1, caret), start: index, end: caret };
    }
    if (TERMINATOR.test(char)) return null;
    index -= 1;
  }
  return null;
}

/**
 * The text with a mention token removed.
 *
 * Choosing a document turns the typed `@query` into a chip, so the raw token
 * must go — otherwise the message would carry both the reference and the
 * half-typed name that produced it.
 *
 * The token sat between two words more often than not (`"Convert @res to PDF"`),
 * so one of the two surrounding spaces is dropped when both are present. That
 * keeps the sentence readable without collapsing runs of whitespace the user may
 * have meant to keep elsewhere in the draft: only the single space created by
 * removing this token is touched.
 */
export function stripMention(text: string, mention: MentionQuery): string {
  const before = text.slice(0, mention.start);
  const after = text.slice(mention.end);
  if (before.endsWith(" ") && after.startsWith(" ")) {
    return before + after.slice(1);
  }
  return before + after;
}
