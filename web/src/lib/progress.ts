/**
 * Coercion and merging for a conversion job's progress percentage.
 *
 * A separate, pure module because three layers need the same two rules and
 * previously each had its own version (or none): the API boundary normalises
 * an SSE frame (`api/normalize.ts`), the job store decides what to render
 * (`jobs/jobStore.ts`), and the assistant card folds frames into its job
 * (`lib/assistantChat.ts`). A percentage that reads differently depending on
 * which surface you look at is exactly the bug these helpers exist to prevent.
 */

/**
 * A finite number from a number *or* a numeric string, else `null`.
 *
 * The SSE stream crosses Redis, which stores every stream field as a string, so
 * a frame can legitimately carry `"25"` where a number belongs (and a job cached
 * in `localStorage` by a bundle built before the API was fixed still holds the
 * string form). Reading only a `number` is what made the progress bar fall back
 * to an indeterminate sweep. A non-numeric value ("a message where a number
 * belongs", an empty string) reads as `null` — "the server sent no number" —
 * never as a fabricated `0`.
 */
export function asNumeric(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * The percentage to store for a job after a frame, given what is already shown.
 *
 * The stream replays a job's history and then streams live, so frames can
 * arrive out of order across a reconnect, and a non-terminal (or `FAILED`)
 * frame can legitimately carry no percentage. Rendering either verbatim made
 * the bar jump *backwards* — a 75% conversion flashing to 25% — or drop to an
 * indeterminate sweep, which reads as "we lost track of it". Progress is
 * cumulative, so the merged value may only move forward; an absent value keeps
 * the last real one.
 *
 * `completed` is the terminal exception: a `COMPLETED` job is 100% by
 * definition, so it is allowed to settle there regardless of the last frame.
 */
export function mergeProgress(
  previous: number | undefined,
  incoming: unknown,
  options: { completed?: boolean } = {},
): number | undefined {
  if (options.completed) return 100;

  const parsed = asNumeric(incoming);
  if (parsed === null) return previous;

  const prior = asNumeric(previous);
  return prior === null ? parsed : Math.max(prior, parsed);
}

/**
 * A whole-number percentage for display, clamped to 0–100, or `null` when there
 * is nothing real to show.
 *
 * Clamped because a bar (and its `aria-valuenow`) must never report 120% or
 * -5%; `null` so a caller renders an indeterminate bar rather than printing a
 * number the backend never sent.
 */
export function displayPercent(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}
