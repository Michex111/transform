/**
 * Turning an API error body into copy a person can act on.
 *
 * The API reports a bad request body as FastAPI's validation array —
 * `{"detail": [{"loc": ["body", "username"], "type": …, "msg": …}, …]}` — and
 * two things have to happen to it before a user sees anything:
 *
 * 1. **Several messages must read as prose.** A single field error is easy; a
 *    form that submits three bad fields at once yields three sentences, and
 *    gluing them together with the wrong separator produces "Username is
 *    required., Password must be at least 8 characters." See
 *    {@link joinValidationMessages}.
 * 2. **A message about one field belongs under that field, not in a toast.**
 *    `loc` is what ties the two together, and it is the only reason `loc` is
 *    worth keeping in the response. See {@link serverFieldErrors}.
 *
 * Kept pure and dependency-free (no `ApiError` import, no DOM) for two reasons:
 * the repo's test environment is `node` with no jsdom, and the decision — where
 * does this sentence go? — is exactly the kind of thing that has gone untested
 * here before.
 */

/** One entry of FastAPI's 422 `detail` array. */
export interface ValidationErrorItem {
  /** Location path, e.g. `["body", "username"]`. The last string is the field. */
  loc: (string | number)[]
  /** Pydantic's machine-readable error kind, e.g. `string_too_short`. */
  type?: string
  /** The message. The API renders this addressed to a person (see the backend's `validation_errors.py`). */
  msg: string
}

/**
 * Extract the validation array from a `detail` value, if that is what it is.
 *
 * Tolerant by design: this runs inside error handling, where throwing would
 * replace the real failure with a parse failure. An entry without a usable
 * `msg` is dropped rather than rendered as `undefined`.
 */
export function validationErrorsFrom(detail: unknown): ValidationErrorItem[] {
  if (!Array.isArray(detail)) return []
  return detail
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .map((entry) => ({
      loc: Array.isArray(entry.loc) ? (entry.loc as (string | number)[]) : [],
      type: typeof entry.type === "string" ? entry.type : undefined,
      msg: typeof entry.msg === "string" ? entry.msg : "",
    }))
    .filter((entry) => entry.msg !== "")
}

/**
 * Join several error messages into one line.
 *
 * The separator depends on what the fragments *are*:
 *
 *  - Messages that already read as complete sentences are separated by a
 *    space, because that reconstructs a paragraph: "Username is required.
 *    Password must be at least 8 characters."
 *  - Anything else is joined with a comma, which is what the API's messages
 *    used to be (bare fragments such as "token must not be empty") and what a
 *    proxy or an older API can still return.
 *
 * The mixed case falls back to commas: a run of fragments is more legible with
 * comma separation than with a space, and only the all-sentences case has an
 * unambiguous answer.
 */
export function joinValidationMessages(messages: string[]): string {
  const cleaned = messages.map((message) => message.trim()).filter(Boolean)
  if (cleaned.length === 0) return ""
  if (cleaned.length === 1) return cleaned[0]

  const allSentences = cleaned.every((message) => /[.!?]$/.test(message))
  return cleaned.join(allSentences ? " " : ", ")
}

/**
 * The trailing field name in a validation `loc` path.
 *
 * `["body", "username"]` → `"username"`. The leading `"body"` is dropped
 * because it names the request part rather than a field — matching what the
 * backend does when it writes the message — so a whole-body error
 * (`["body"]`) yields `undefined` instead of claiming the body is a field named
 * "body". A trailing number means the error is about an item in a list
 * (`["body", "items", 0]`), whose field is the collection; that also returns
 * `undefined` rather than inventing a name, so the caller falls back to showing
 * the message as-is.
 */
export function fieldNameFromLoc(loc: (string | number)[]): string | undefined {
  // Only the first segment: a nested object could legitimately have a *field*
  // called `body`, and stripping that would lose the real name.
  const parts = loc[0] === "body" ? loc.slice(1) : loc;
  const last = parts[parts.length - 1];
  return typeof last === "string" ? last : undefined
}

/**
 * Split validation errors into "belongs under this field" and "everything else".
 *
 * `fields` is the set of inputs the caller can actually render an error for. An
 * error naming any other field is returned in `unplaced` instead of being
 * dropped — silently discarding the API's only explanation of what went wrong
 * would be worse than showing it generically.
 *
 * When several errors name the same field the first wins: the field has room for
 * one line, and the server's errors are ordered by field position.
 */
export function serverFieldErrors<F extends string>(
  items: ValidationErrorItem[],
  fields: readonly F[],
): { fieldErrors: Partial<Record<F, string>>; unplaced: string[] } {
  const fieldErrors: Partial<Record<F, string>> = {}
  const unplaced: string[] = []

  for (const item of items) {
    const name = fieldNameFromLoc(item.loc)
    const match = name && (fields as readonly string[]).includes(name) ? (name as F) : undefined
    if (match && fieldErrors[match] === undefined) {
      fieldErrors[match] = item.msg
    } else {
      unplaced.push(item.msg)
    }
  }

  return { fieldErrors, unplaced }
}
